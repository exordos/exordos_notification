# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").

import pathlib
import runpy
from unittest import mock
import uuid

import pytest
import requests
from restalchemy.dm import filters

from exordos_notification.common import transports
from exordos_notification.dm import models
from exordos_notification.services.builders import agents


def firebase_provider():
    return models.Provider(
        name="Firebase",
        project_id=uuid.uuid4(),
        protocol=models.FirebaseProtocol(),
    )


def firebase_content(token, device_uuid=None):
    return models.RenderedFirebaseAndroidContent(
        device_uuid=device_uuid or uuid.uuid4(),
        registration_token=token,
        data={"payload": "synthetic-encrypted-payload"},
        priority="high",
    )


def inactive_failure(content):
    return transports.DeliveryError(
        "registration_token_inactive",
        [
            {
                "device_uuid": str(content.device_uuid),
                "code": "registration_token_inactive",
            }
        ],
        permanent=True,
    )


@pytest.mark.parametrize("old_status", ["ACTIVE", "IN_PROGRESS"])
@pytest.mark.parametrize("keep_email_binding", [False, True])
def test_upgrade_removes_legacy_delivery_without_resending(
    database, old_status, keep_email_binding
):
    provider = firebase_provider()
    project = provider.project_id
    event_type = models.EventType(project_id=project, name="Push")
    user = uuid.uuid4()
    event = models.Event(
        project_id=project,
        event_type=event_type,
        exchange=models.UserExchange(user_uuid=user),
        event_params={},
        status=old_status,
    )
    content = models.FirebaseAndroidContent(data={"payload": "{{ payload }}"})
    old_template = models.Template(
        project_id=project,
        name="Legacy",
        provider=provider,
        event_type=event_type,
        params={},
        content=content,
        is_default=False,
    )
    current = models.Template(
        project_id=project,
        name="Firebase",
        provider=provider,
        event_type=event_type,
        params={},
        content=content,
        is_default=True,
    )
    legacy_snapshot = models.RenderedEvent(
        provider=provider,
        content=firebase_content("legacy"),
        event_id=event.uuid,
        user_context={},
    )
    current_snapshot = models.RenderedEvent(
        provider=provider,
        content=firebase_content("current"),
        event_id=uuid.uuid4(),
        user_context={},
    )
    binding = models.Binding(
        project_id=project,
        event_type=event_type,
        template=old_template,
        user=user,
    )
    smtp = models.Provider(
        project_id=project,
        name="SMTP",
        protocol=models.SimpleSmtpProtocol(
            host="smtp.example.com",
            port=25,
            noreply_email_address="sender@example.com",
        ),
    )
    email = models.Template(
        project_id=project,
        name="Email",
        provider=smtp,
        event_type=event_type,
        is_default=True,
        params={},
        content=models.EmailContent(title="Email", bodies=[]),
    )
    expected_templates = {current.uuid}
    with database.session_manager() as session:
        for row in (
            provider,
            event_type,
            event,
            old_template,
            binding,
            legacy_snapshot,
            current_snapshot,
            smtp,
            email,
        ):
            row.insert(session=session)
        session.execute(
            """UPDATE templates SET content = '{"kind":"mobile_push"}' WHERE uuid = %s""",
            (str(old_template.uuid),),
        )
        session.execute(
            """UPDATE rendered_events SET content = '{"kind":"rendered_mobile_push"}' WHERE uuid = %s""",
            (str(legacy_snapshot.uuid),),
        )
        if keep_email_binding:
            models.Binding(
                project_id=project,
                event_type=event_type,
                template=email,
                user=user,
            ).insert(session=session)
            expected_templates.add(email.uuid)
        path = (
            pathlib.Path(__file__).resolve().parents[3]
            / "migrations"
            / "0003-Remove-legacy-mobile-push-rows-a01606.py"
        )
        runpy.run_path(str(path))["migration_step"].upgrade(session)
        # The producer can provision the replacement after Notification upgrades.
        current.insert(session=session)

    assert {template.uuid for template in event.get_templates()} == expected_templates
    assert (
        models.RenderedEvent.objects.get_one_or_none(
            filters={"uuid": filters.EQ(legacy_snapshot.uuid)}
        )
        is None
    )
    assert (
        models.RenderedEvent.objects.get_one(
            filters={"uuid": filters.EQ(current_snapshot.uuid)}
        )
        == current_snapshot
    )
    retired = models.Event.objects.get_one(filters={"uuid": filters.EQ(event.uuid)})
    assert retired.next_retry_at > retired.last_retry_at
    assert retired.status == ("ACTIVE" if old_status == "ACTIVE" else "ERROR")


def test_inactive_token_is_cached_without_storing_the_token(database):
    provider = firebase_provider()
    token = f"test-{uuid.uuid4()}"
    content = firebase_content(token)
    failure = inactive_failure(content)
    session = mock.Mock()

    with (
        mock.patch.object(
            transports,
            "firebase_session",
            return_value=(session, "firebase-project"),
        ),
        mock.patch.object(models.FirebaseProtocol, "send", side_effect=failure) as send,
    ):
        for _ in range(2):
            with pytest.raises(transports.DeliveryError) as raised:
                provider.send(content, {})
            assert raised.value.code == "registration_token_inactive"

    assert send.call_count == 1
    with database.session_manager() as db_session:
        row = db_session.execute(
            "SELECT token_fingerprint FROM inactive_tokens WHERE scope = %s",
            ("firebase:firebase-project",),
        ).fetchone()
    assert row is not None
    assert row["token_fingerprint"] != token


def test_inactive_device_error_reaches_parent_and_stops_retrying(database, http_server):
    provider = firebase_provider()
    event_type = models.EventType(project_id=provider.project_id)
    event = models.Event(
        project_id=provider.project_id,
        event_type=event_type,
        event_params={},
    )
    content = firebase_content(f"test-{uuid.uuid4()}")
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

    with (
        mock.patch.object(
            transports,
            "firebase_session",
            return_value=(mock.Mock(), "firebase-project"),
        ),
        mock.patch.object(
            models.FirebaseProtocol,
            "send",
            side_effect=inactive_failure(content),
        ),
    ):
        rendered.send()

    assert rendered.next_retry_at > rendered.last_retry_at
    agents.EventBuilderAgent(iam_client=mock.Mock())._sync_event_statuses()
    saved = models.Event.objects.get_one(filters={"uuid": filters.EQ(event.uuid)})
    assert saved.status == "ERROR"
    assert saved.status_description == "registration_token_inactive"
    assert saved.errors == [
        {
            "device_uuid": str(content.device_uuid),
            "code": "registration_token_inactive",
            "permanent": True,
        }
    ]

    base, driver = http_server
    response = requests.get(
        f"{base}/v1/events/{event.uuid}",
        headers=driver.headers(
            [f"exordos_notification.event_type_{event_type.uuid}.read"],
            project_uuid=event.project_id,
        ),
        timeout=5,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status_description"] == "registration_token_inactive"
    assert response.json()["errors"] == saved.errors
