# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").

import runpy
import uuid
from pathlib import Path
from unittest import mock

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


def test_upgrade_deactivates_legacy_default_template(database):
    project_id = uuid.uuid4()
    provider = models.Provider(
        name="Firebase",
        project_id=project_id,
        protocol=models.FirebaseProtocol(),
    )
    event_type = models.EventType(project_id=project_id, name="push")
    legacy = models.Template(
        project_id=project_id,
        name="legacy",
        provider=provider,
        event_type=event_type,
        params={},
        content=models.LegacyMobilePushContent(),
        is_default=True,
    )
    current = models.Template(
        project_id=project_id,
        name="current",
        provider=provider,
        event_type=event_type,
        params={},
        content=models.FirebaseAndroidContent(data={"payload": "{{ payload }}"}),
        is_default=True,
    )
    with database.session_manager() as session:
        provider.insert(session=session)
        event_type.insert(session=session)
        legacy.insert(session=session)
        current.insert(session=session)
        migration_path = (
            Path(__file__).resolve().parents[3]
            / "migrations"
            / "0002-Deactivate-legacy-mobile-push-templates-29a44a.py"
        )
        runpy.run_path(str(migration_path))["migration_step"].upgrade(session)

    event = models.Event(
        project_id=project_id,
        event_type=event_type,
        event_params={"payload": "encrypted"},
    )
    assert [template.uuid for template in event.get_templates()] == [current.uuid]


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
