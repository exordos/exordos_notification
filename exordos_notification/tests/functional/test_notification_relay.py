# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.

import json
import uuid
from unittest import mock

import httpcore
import pytest
from restalchemy.dm import filters

from exordos_notification.common import apns
from exordos_notification.common import transports
from exordos_notification.dm import models
from exordos_notification.services.builders import agents as builders
from exordos_notification.services.providers import agents as providers


@pytest.mark.parametrize("transport", ("fcm", "apns"))
@pytest.mark.parametrize("inactive", (False, True))
def test_relay_forwards_once_and_follows_upstream_status(
    http_server,
    monkeypatch,
    tmp_path,
    inactive,
    transport,
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
    credentials = "RELAY_CREDENTIALS_FILE"
    template_content = models.NotificationRelayContent()
    protocol = models.NotificationRelayProtocol()
    device_id = uuid.uuid4()
    expected_params = {
        "device_uuid": str(device_id),
        "registration_token": "ab" * 32,
        "payload": "encrypted-fixture",
        "priority": "high",
    }
    if transport == "apns":
        receiver_provider = models.Provider(
            project_id=project,
            name="APNs recipient",
            protocol=models.ApnsProtocol(topic="com.example.app"),
        )
        receiver_provider.insert()
        models.Template(
            project_id=project,
            name="APNs recipient",
            event_type=receiver,
            provider=receiver_provider,
            params={},
            content=models.ApnsContent(),
            is_default=True,
        ).insert()
        apple = mock.Mock()
        apple.authorization.return_value = "bearer synthetic"
        apple.client.request.return_value = httpcore.Response(
            410 if inactive else 200,
            content=b'{"reason":"Unregistered"}' if inactive else b"",
        )
        apple.client.request.return_value.read()
        monkeypatch.setattr(apns, "connection", lambda environment: apple)

        credentials = "APNS_RELAY_CREDENTIALS_FILE"
        template_content = models.ApnsContent()
        protocol = models.NotificationRelayProtocol(transport="apns")
        expected_params.update(
            payload='{"aps":{"alert":{"loc-key":"NEW_ALERT"}},"data":"fixture"}',
            apns_environment="sandbox",
            apns_collapse_id="a" * 64,
        )
    monkeypatch.setattr(transports, credentials, str(config_path), raising=False)
    transports.relay_destination.cache_clear()
    event = models.Event(
        project_id=uuid.uuid4(),
        event_type=models.EventType(project_id=project, name="Local push"),
        event_params=dict(expected_params, extra="discarded"),
    )
    provider = models.Provider(
        project_id=project,
        name="Relay",
        protocol=protocol,
    )
    template = models.Template(
        project_id=project,
        name="Relay",
        event_type=event.event_type,
        provider=provider,
        params={},
        content=template_content,
        is_default=True,
    )
    with mock.patch.object(models.Event, "get_templates", return_value=[template]):
        relay = event.render(mock.Mock())[0]
    try:
        with pytest.raises(transports.DeliveryPending):
            provider.send(relay.content, {})
        upstream = models.Event.objects.get_one(
            filters={"uuid": filters.EQ(event.uuid)}
        )
        assert upstream.event_type.uuid == receiver.uuid
        assert upstream.event_params == expected_params
        with pytest.raises(transports.DeliveryPending):
            provider.send(relay.content, {})
        if transport == "apns":
            builder = builders.EventBuilderAgent(iam_client=mock.Mock())
            builder._iteration()
            providers.SMTPAgent()._iteration()
            builder._iteration()
            apple.client.request.assert_called_once()
            call = apple.client.request.call_args
            assert call.args == (
                "POST",
                "https://api.sandbox.push.apple.com/3/device/" + "ab" * 32,
            )
            assert call.kwargs["headers"]["apns-topic"] == "com.example.app"
            assert call.kwargs["headers"]["apns-collapse-id"] == "a" * 64
            assert call.kwargs["content"] == expected_params["payload"].encode()
        else:
            upstream.status = (
                models.Event.STATUS.ERROR.value
                if inactive
                else models.Event.STATUS.ACTIVE.value
            )
            if inactive:
                upstream.errors = [
                    {
                        "code": "registration_token_inactive",
                        "device_uuid": str(device_id),
                        "permanent": True,
                    }
                ]
            upstream.update()
        if not inactive:
            assert provider.send(relay.content, {}) is None
            return

        with pytest.raises(transports.DeliveryError) as raised:
            provider.send(relay.content, {})
        assert raised.value.permanent is True
        assert raised.value.errors == [
            {
                "code": "registration_token_inactive",
                "device_uuid": str(device_id),
            }
        ]
        next_event = models.Event(
            project_id=event.project_id,
            event_type=event.event_type,
            event_params=event.event_params,
        )
        with mock.patch.object(models.Event, "get_templates", return_value=[template]):
            next_delivery = next_event.render(mock.Mock())[0]
        if transport == "apns":
            # An iOS device can register the same APNs token again.
            with pytest.raises(transports.DeliveryPending):
                provider.send(next_delivery.content, {})
            assert models.Event.objects.get_one(
                filters={"uuid": filters.EQ(next_event.uuid)}
            ).event_params == expected_params
        else:
            with pytest.raises(transports.DeliveryError) as cached:
                provider.send(next_delivery.content, {})
            assert cached.value.errors[0]["code"] == "registration_token_inactive"
    finally:
        transports.relay_destination.cache_clear()
