#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License.

import json
from unittest import mock

import pytest

from exordos_notification.common import transports
from exordos_notification.dm import models
from exordos_notification.services.builders import agents
from exordos_notification.tests.functional.test_push_api import source_headers
from exordos_notification.tests.unit.test_push_transports import push_event


def test_relay_uses_real_v1_http_and_propagates_partial_inactive_error(
    http_server, tmp_path
):
    base, driver = http_server
    event, template = push_event()
    payload = event.notification_event()
    auth_file = tmp_path / "relay-auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "base_url": "http://unused.invalid/api/core",
                "access_token": source_headers(driver, payload)[
                    "Authorization"
                ].split()[1],
            }
        )
    )
    auth_file.chmod(0o600)
    relay = models.Provider(
        name="Customer relay",
        project_id=event.project_id,
        protocol=models.NotificationProtocol(endpoint=base, auth_file=str(auth_file)),
    )
    content = models.RenderedNotificationContent(payload=payload)
    # Real SDK, HTTP listener, JWT policies and PostgreSQL; no relay-client mock.
    for _ in range(2):
        with pytest.raises(transports.DeliveryPending):
            relay.send(content, {})
    saved = models.Event.objects.get_one(filters={"uuid": event.uuid})
    assert saved.event_params == event.event_params
    assert saved.exchange.user_uuid == event.exchange.user_uuid
    assert len(models.Event.objects.get_all(filters={"uuid": event.uuid})) == 1

    template.provider.insert()
    with mock.patch.object(models.Event, "get_templates", return_value=[template]):
        rendered = saved.render(mock.Mock())
    for item in rendered:
        item.insert()
    rejected, accepted = rendered
    error = {
        "device_uuid": str(rejected.content.device_uuid),
        "code": "registration_token_inactive",
        "permanent": True,
    }
    with mock.patch.object(
        transports,
        "send_firebase",
        side_effect=transports.DeliveryError(
            "registration_token_inactive", [error], permanent=True
        ),
    ):
        rejected.send()
    builder = agents.EventBuilderAgent(iam_client=mock.Mock())
    builder._sync_event_statuses()
    with pytest.raises(transports.DeliveryPending) as pending:
        relay.send(content, {})
    assert pending.value.errors == [error]
    inactive = models.InactiveToken.identify(
        f"notification:{base}", rejected.content.registration_token
    )
    assert models.InactiveToken.objects.get_one(filters={"uuid": inactive.uuid})

    with mock.patch.object(transports, "send_firebase"):
        accepted.send()
    builder._sync_event_statuses()
    with pytest.raises(transports.DeliveryError) as failed:
        relay.send(content, {})
    assert failed.value.permanent
    assert failed.value.errors == [error]
    assert content.payload == payload
    transports.relay_client.cache_clear()
