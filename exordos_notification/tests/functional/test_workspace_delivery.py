# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").

import datetime
import json
from unittest import mock
import uuid

import pytest
import requests
from restalchemy.dm import filters

from exordos_notification.clients import workspace
from exordos_notification.dm import models
from exordos_notification.services.builders import agents as builder
from exordos_notification.services.providers import agents as provider


@pytest.mark.parametrize(
    ("kind", "outcome"),
    [
        ("workspace_stream_message", "retry"),
        ("workspace_direct_message", "retry"),
        ("workspace_stream_message", "forbidden"),
        ("workspace_direct_message", "wrong_peer"),
    ],
)
def test_workspace_event_delivery(http_server, monkeypatch, tmp_path, kind, outcome):
    base, driver = http_server
    project = uuid.uuid4()
    event_type = models.EventType(project_id=project)
    event_type.insert()
    destination = models.Provider(
        project_id=project,
        name="Workspace",
        protocol=models.Provider.properties.properties["protocol"]
        .get_property_type()
        .from_simple_type({"kind": "workspace"}),
    )
    destination.insert()
    direct = kind == "workspace_direct_message"
    models.Template(
        project_id=project,
        name="Workspace notification",
        params={},
        event_type=event_type,
        provider=destination,
        content=models.Template.properties.properties["content"]
        .get_property_type()
        .from_simple_type({"kind": kind, "content": "**{{ message }}**"}),
        is_default=True,
    ).insert()

    monkeypatch.setattr(workspace, "CREDENTIALS_DIR", tmp_path)
    credentials = tmp_path / str(project) / f"{destination.uuid}.json"
    credentials.parent.mkdir()
    origin = "https://workspace.example.test"
    first_token = "synthetic-first"
    second_token = "synthetic-renewed"
    credentials.write_text(
        json.dumps({"endpoint": origin, "access_token": first_token}), encoding="utf-8"
    )

    stream_id, topic_id, user_id = [str(uuid.uuid4()) for _ in range(3)]
    request = requests.Session.request
    messages, lookups = [], []

    def send(session, method, url, **kwargs):
        if not url.startswith(origin):
            return request(session, method, url, **kwargs)
        assert method in {"POST", "post"}
        assert kwargs["allow_redirects"] is False
        response = requests.Response()
        response.status_code = 201
        if url == f"{origin}/api/workspace/v1/messenger/streams/":
            lookups.append(kwargs["json"])
            response._content = json.dumps(
                {
                    "uuid": stream_id,
                    "default_topic_uuid": topic_id,
                    "private": True,
                    "direct_user_uuid": (
                        str(uuid.uuid4()) if outcome == "wrong_peer" else user_id
                    ),
                }
            ).encode()
            return response
        assert url == f"{origin}/api/workspace/v1/messenger/messages/"
        messages.append((kwargs["json"], kwargs["headers"]["Authorization"]))
        if outcome == "forbidden":
            response.status_code = 403
            response._content = first_token.encode()
            return response
        if len(messages) == 1:
            # Workspace might have accepted the message before the connection failed.
            raise requests.Timeout(first_token)
        response._content = json.dumps(
            {
                "uuid": str(uuid.uuid4()),
                "stream_uuid": stream_id,
                "topic_uuid": topic_id,
            }
        ).encode()
        return response

    monkeypatch.setattr(requests.Session, "request", send)
    event_id = str(uuid.uuid4())
    headers = driver.headers(
        [
            f"exordos_notification.event_type_{event_type.uuid}.create",
            f"exordos_notification.event_type_{event_type.uuid}.read",
        ]
    )
    response = requests.post(
        f"{base}/v1/events/",
        json={
            "uuid": event_id,
            "project_id": str(project),
            "event_type": f"/v1/event_types/{event_type.uuid}",
            "event_params": {
                "message": "Проверка",
                "stream_uuid": stream_id,
                "topic_uuid": topic_id,
                "user_uuid": user_id,
            },
        },
        headers=headers,
        timeout=5,
    )
    assert response.status_code == 201, response.text

    iam = mock.Mock()
    event_builder = builder.EventBuilderAgent(iam_client=iam)
    event_builder._iteration()
    sender = provider.SMTPAgent()
    sender._iteration()
    event_builder._iteration()
    event_url = f"{base}/v1/events/{event_id}"
    result = requests.get(event_url, headers=headers, timeout=5).json()
    expected_error = {
        "forbidden": {"code": "workspace_http_403", "permanent": True},
        "retry": {"code": "workspace_transport_error", "permanent": False},
        "wrong_peer": {"code": "workspace_invalid_response", "permanent": False},
    }[outcome]
    assert result["status"] == "ERROR"
    assert result["errors"] == [expected_error]
    assert first_token not in json.dumps(result)
    iam.get_user.assert_not_called()
    if outcome == "wrong_peer":
        assert not messages
        assert len(lookups) == 1
        return

    rendered = models.RenderedEvent.objects.get_one(
        filters={"event_id": filters.EQ(uuid.UUID(event_id))}
    )
    if outcome == "forbidden":
        assert rendered.next_retry_at > rendered.last_retry_at
        assert len(messages) == 1
        return

    credentials.write_text(
        json.dumps({"endpoint": origin, "access_token": second_token}), encoding="utf-8"
    )
    rendered.next_retry_at = datetime.datetime.now(datetime.timezone.utc)
    rendered.update()
    sender._iteration()
    event_builder._iteration()
    result = requests.get(event_url, headers=headers, timeout=5).json()
    assert result["status"] == "ACTIVE"
    assert result["errors"] == []
    first, second = messages
    assert first[0] == second[0]
    uuid.UUID(first[0]["uuid"])
    assert first[0] == {
        "uuid": first[0]["uuid"],
        "stream_uuid": stream_id,
        "topic_uuid": topic_id,
        "payload": {"kind": "markdown", "content": "**Проверка**"},
    }
    assert first[1] == f"Bearer {first_token}"
    assert second[1] == f"Bearer {second_token}"
    assert len(lookups) == (2 if direct else 0)
    assert all(payload["direct_user_uuid"] == user_id for payload in lookups)
