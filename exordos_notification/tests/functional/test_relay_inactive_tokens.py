# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").
import uuid
from unittest import mock

import pytest

from exordos_notification.common import transports
from exordos_notification.dm import models


def test_relay_learns_inactive_token_while_other_device_is_pending(database):
    from exordos_notification.tests.unit.test_push_transports import push_event

    event, _ = push_event()
    payload = event.notification_event()
    rejected, accepted = payload["event_params"]["devices"]
    provider = models.Provider(
        name="Relay",
        project_id=event.project_id,
        protocol=models.NotificationProtocol(
            endpoint=f"https://{uuid.uuid4()}.example.com", auth_file="/test/auth.json"
        ),
    )
    content = models.RenderedNotificationContent(payload=payload)
    errors = [
        {
            "device_uuid": rejected["device_uuid"],
            "code": "registration_token_inactive",
            "permanent": True,
        }
    ]
    with (
        mock.patch.object(
            models.NotificationProtocol,
            "send",
            side_effect=transports.DeliveryPending(errors),
        ),
        pytest.raises(transports.DeliveryPending) as waiting,
    ):
        provider.send(content, {})
    assert waiting.value.errors == errors
    with (
        mock.patch.object(models.NotificationProtocol, "send") as send,
        pytest.raises(transports.DeliveryError) as failed,
    ):
        provider.send(content, {})
    assert failed.value.permanent
    assert send.call_args.kwargs["content"].payload["event_params"]["devices"] == [
        accepted
    ]
    assert payload["event_params"]["devices"] == [rejected, accepted]

