# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").
import uuid

import requests

from exordos_notification.dm import models


def test_public_relay_ingress_cannot_submit_legacy_email(http_server):
    base, driver = http_server
    event_type = models.EventType(project_id=uuid.uuid4())
    event_type.insert()
    payload = {
        "project_id": str(event_type.project_id),
        "event_type": f"/v1/event_types/{event_type.uuid}",
        "event_params": {},
        "exchange": {"kind": "Dummy"},
    }
    for headers in ({}, driver.headers()):
        result = requests.post(
            base + "/v1/events/",
            json=payload,
            headers={**headers, "X-Notification-Relay": "1"},
            timeout=5,
        )
        assert result.status_code == 403, result.text
    # Internal Core calls use a token restricted to this email event type.
    headers = driver.headers(
        [
            f"exordos_notification.event_type_{event_type.uuid}.create",
            f"exordos_notification.event_type_{event_type.uuid}.read",
        ]
    )
    internal = requests.post(
        base + "/v1/events/", json=payload, headers=headers, timeout=5
    )
    assert internal.status_code == 201, internal.text
    assert (
        requests.get(
            base + "/v1/events/" + internal.json()["uuid"],
            headers={**headers, "X-Notification-Relay": "1"},
            timeout=5,
        ).status_code
        == 403
    )

