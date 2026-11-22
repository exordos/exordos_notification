# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.

import json
import uuid
from unittest import mock

import pytest
from restalchemy.dm import filters

from exordos_notification.common import transports
from exordos_notification.dm import models


@pytest.mark.parametrize("inactive", (False, True))
def test_relay_forwards_once_and_follows_upstream_status(
    http_server,
    monkeypatch,
    tmp_path,
    inactive,
):
    endpoint, iam = http_server
    project = uuid.uuid4()
    receiver = models.EventType(project_id=project, name="Relay destination")
    receiver.insert()
    headers = iam.headers(
        permissions=[
            f"exordos_notification.event_type_{receiver.uuid}.create",
            f"exordos_notification.event_type_{receiver.uuid}.read",
        ]
    )
    config_path = tmp_path / "upstream.json"
    config_path.write_text(
        json.dumps(
            {
                "notification_endpoint": endpoint,
                "iam_endpoint": endpoint,
                "event_type_uuid": str(receiver.uuid),
                "project_uuid": str(project),
                "access_token": headers["Authorization"].removeprefix("Bearer "),
            }
        )
    )
    monkeypatch.setattr(transports, "RELAY_CREDENTIALS_FILE", str(config_path))
    transports.relay_destination.cache_clear()
    device_id = uuid.uuid4()
    event = models.Event(
        project_id=uuid.uuid4(),
        event_type=models.EventType(project_id=project, name="Local push"),
        event_params={
            "device_uuid": str(device_id),
            "registration_token": "fixture-device-address",
            "payload": "encrypted-fixture",
            "priority": "high",
            "extra": "discarded",
        },
    )
    provider = models.Provider(
        project_id=project,
        name="Relay",
        protocol=models.NotificationRelayProtocol(),
    )
    template = models.Template(
        project_id=project,
        name="Relay",
        event_type=event.event_type,
        provider=provider,
        params={},
        content=models.NotificationRelayContent(),
        is_default=True,
    )
    with mock.patch.object(models.Event, "get_templates", return_value=[template]):
        relay = event.render(mock.Mock())[0]
    assert relay.content.KIND == "rendered_notification_relay"
    try:
        with pytest.raises(transports.DeliveryPending):
            provider.send(relay.content, {})
        upstream = models.Event.objects.get_one(
            filters={"uuid": filters.EQ(event.uuid)}
        )
        assert upstream.event_type.uuid == receiver.uuid
        assert upstream.event_params == {
            "device_uuid": str(device_id),
            "registration_token": "fixture-device-address",
            "payload": "encrypted-fixture",
            "priority": "high",
        }
        with pytest.raises(transports.DeliveryPending):
            provider.send(relay.content, {})
        if not inactive:
            upstream.status = models.Event.STATUS.ACTIVE.value
            upstream.update()
            assert provider.send(relay.content, {}) is None
            return

        upstream.status = models.Event.STATUS.ERROR.value
        upstream.errors = [
            {
                "code": "registration_token_inactive",
                "device_uuid": str(uuid.uuid4()),
                "permanent": True,
            },
            {
                "code": "registration_token_inactive",
                "device_uuid": str(device_id),
                "permanent": True,
            },
        ]
        upstream.update()
        with pytest.raises(transports.DeliveryError) as raised:
            provider.send(relay.content, {})
        assert raised.value.permanent is True
        assert raised.value.errors == [
            {
                "code": "registration_token_inactive",
                "device_uuid": str(device_id),
            }
        ]
        with pytest.raises(transports.DeliveryError) as cached:
            provider.send(relay.content, {})
        assert cached.value.errors[0]["code"] == "registration_token_inactive"
    finally:
        transports.relay_destination.cache_clear()


def test_retired_user_binding_falls_back_to_current_firebase_template(database):
    project = uuid.uuid4()
    event_type = models.EventType(project_id=project, name="Push")
    event = models.Event(
        project_id=project,
        event_type=event_type,
        exchange=models.UserExchange(user_uuid=uuid.uuid4()),
        event_params={},
    )
    provider = models.Provider(
        project_id=project, name="Firebase", protocol=models.FirebaseProtocol()
    )
    legacy = models.Template(
        project_id=project,
        name="Legacy",
        provider=provider,
        event_type=event_type,
        is_default=False,
        params={},
        content=models.LegacyMobilePushContent(),
    )
    current = models.Template(
        project_id=project,
        name="Firebase",
        provider=provider,
        event_type=event_type,
        is_default=True,
        params={},
        content=models.FirebaseAndroidContent(data={"payload": "encrypted"}),
    )
    provider.insert()
    event_type.insert()
    legacy.insert()
    current.insert()
    models.Binding(
        project_id=project,
        event_type=event_type,
        template=legacy,
        user=event.exchange.user_uuid,
    ).insert()
    assert event.get_templates() == [current]
