#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License.

import datetime
import uuid
from unittest import mock

import pytest

from exordos_notification.common import transports
from exordos_notification.dm import models
from exordos_notification.services.builders import agents


def firebase_provider():
    return models.Provider(
        name="Firebase",
        project_id=uuid.uuid4(),
        protocol=models.FirebaseProtocol(
            firebase_project_id=f"test-{uuid.uuid4()}",
            credentials_file="/test/credentials.json",
        ),
    )


def push_content(token, device_uuid=None):
    return models.RenderedMobilePushContent(
        device_uuid=device_uuid or uuid.uuid4(),
        registration_token=token,
        payload={"kind": "workspace_message_create"},
        expires_at=datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(hours=1),
    )


def test_inactive_token_is_durable_and_new_token_is_not_blocked(database):
    provider = firebase_provider()
    token = f"test-{uuid.uuid4()}"
    content = push_content(token)
    failure = transports.DeliveryError(
        "registration_token_inactive",
        [
            {
                "device_uuid": str(content.device_uuid),
                "code": "registration_token_inactive",
            }
        ],
        permanent=True,
    )
    with mock.patch.object(transports, "send_firebase", side_effect=failure) as send:
        for _ in range(2):
            with pytest.raises(transports.DeliveryError):
                provider.send(content, {})
        assert send.call_count == 1

    with database.session_manager() as session:
        row = session.execute(
            "SELECT token_fingerprint FROM inactive_tokens WHERE scope = %s",
            (f"firebase:{provider.protocol.firebase_project_id}",),
        ).fetchone()
        assert row is not None
        assert row["token_fingerprint"] != token

    with mock.patch.object(transports, "send_firebase") as send:
        provider.send(push_content(f"new-{uuid.uuid4()}", content.device_uuid), {})
        send.assert_called_once()


def test_repeated_invalidation_does_not_abort_a_transaction(database):
    key = models.InactiveToken.identify(f"test:{uuid.uuid4()}", "test-registration")
    key.remember()
    key.remember()
    with database.session_manager() as session:
        assert (
            session.execute(
                "SELECT count(*) AS count FROM inactive_tokens WHERE uuid = %s",
                (str(key.uuid),),
            ).fetchone()["count"]
            == 1
        )


def test_device_error_reaches_parent_event_and_stops_retrying(database):
    provider = firebase_provider()
    event_type = models.EventType(project_id=provider.project_id)
    event = models.Event(
        project_id=provider.project_id, event_type=event_type, event_params={}
    )
    content = push_content(f"test-{uuid.uuid4()}")
    rendered = models.RenderedEvent(
        provider=provider,
        content=content,
        event_id=event.uuid,
        user_context={},
    )
    with database.session_manager() as session:
        provider.insert(session=session)
        event_type.insert(session=session)
        event.insert(session=session)
        rendered.insert(session=session)
    failure = transports.DeliveryError(
        "registration_token_inactive",
        [
            {
                "device_uuid": str(content.device_uuid),
                "code": "registration_token_inactive",
            }
        ],
        permanent=True,
    )
    with mock.patch.object(transports, "send_firebase", side_effect=failure):
        rendered.send()
    assert rendered.next_retry_at == rendered.last_retry_at
    agent = agents.EventBuilderAgent(iam_client=mock.Mock())
    agent._sync_event_statuses()
    saved = models.Event.objects.get_one(filters={"uuid": event.uuid})
    assert saved.status == "ERROR"
    assert saved.errors == [dict(item, permanent=True) for item in failure.errors]


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
            transports, "send_relay", side_effect=transports.DeliveryPending(errors)
        ),
        pytest.raises(transports.DeliveryPending) as waiting,
    ):
        provider.send(content, {})
    assert waiting.value.errors == errors
    with (
        mock.patch.object(transports, "send_relay") as send,
        pytest.raises(transports.DeliveryError) as failed,
    ):
        provider.send(content, {})
    assert failed.value.permanent
    assert send.call_args.args[1].payload["event_params"]["devices"] == [accepted]
    assert payload["event_params"]["devices"] == [rejected, accepted]
