# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").

import datetime
import json
from unittest import mock
import uuid

import httpcore
import pytest
import requests

from exordos_notification.common import apns
from exordos_notification.dm import models
from exordos_notification.services.builders import agents as builder
from exordos_notification.services.providers import agents as provider


@pytest.mark.parametrize("status", [200, 410])
def test_apns_event_status_is_reported_through_notification(
    http_server, monkeypatch, caplog, status
):
    base, driver = http_server
    project = uuid.uuid4()
    event_type = models.EventType(project_id=project)
    event_type.insert()
    destination = models.Provider(
        project_id=project,
        name="Test APNs",
        protocol=models.ApnsProtocol(topic="com.example.app"),
    )
    destination.insert()
    models.Template(
        project_id=project,
        name="Test APNs topic",
        params={},
        event_type=event_type,
        provider=destination,
        content=models.ApnsContent(),
        is_default=True,
    ).insert()

    apns_response = httpcore.Response(status, content=b'{"reason":"Unregistered"}')
    apns_response.read()
    connection = mock.Mock()
    connection.authorization.return_value = "bearer synthetic"
    connection.client.request.return_value = apns_response
    monkeypatch.setattr(apns, "connection", lambda environment: connection)
    iam = mock.Mock()
    event_builder = builder.EventBuilderAgent(iam_client=iam)
    event_id, device_id = str(uuid.uuid4()), str(uuid.uuid4())
    device_token = "98" * 32
    payload = {
        "aps": {"alert": {"loc-key": "NEW_ALERT"}, "sound": "chime.aiff"},
        "data": {"ciphertext": "synthetic-ciphertext"},
    }
    expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        minutes=15
    )
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
            "exchange": {"kind": "User", "user_uuid": str(uuid.uuid4())},
            "event_params": {
                "device_uuid": device_id,
                "registration_token": device_token,
                "payload": json.dumps(payload),
                "apns_environment": "sandbox",
                "apns_collapse_id": "a" * 64,
                "priority": "high",
            },
            "last_retry_at": expires.isoformat(),
        },
        headers=headers,
        timeout=5,
    )
    assert response.status_code == 201, response.text

    with caplog.at_level("INFO"):
        event_builder._iteration()
        provider.SMTPAgent()._iteration()
        event_builder._iteration()
    assert device_token not in caplog.text
    assert "synthetic-ciphertext" not in caplog.text
    iam.get_user.assert_not_called()
    connection.client.request.assert_called_once()
    sent = connection.client.request.call_args.kwargs
    assert sent["headers"]["apns-topic"] == "com.example.app"
    assert json.loads(sent["content"]) == payload
    response = requests.get(f"{base}/v1/events/{event_id}", headers=headers, timeout=5)
    assert response.status_code == 200, response.text
    if status == 200:
        assert response.json()["status"] == "ACTIVE"
    else:
        assert response.json()["status"] == "ERROR"
        assert response.json()["errors"] == [
            {
                "code": "registration_token_inactive",
                "device_uuid": device_id,
                "permanent": True,
            }
        ]
