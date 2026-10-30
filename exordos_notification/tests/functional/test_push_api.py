#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License.

import pathlib
import uuid

import jinja2
import pytest
import requests
import yaml

from exordos_notification.common import constants
from exordos_notification.dm import models
from exordos_notification.tests.unit.test_push_transports import push_event


def source_headers(driver, payload):
    source = payload["event_params"]["organization_uuid"]
    return driver.headers(
        [
            "exordos_notification.event_type.read",
            f"exordos_notification.source_{source}.create",
            f"exordos_notification.source_{source}.read",
        ],
        project_scoped=False,
    )


def test_topic_update_uses_the_same_authenticated_event_api(http_server):
    base, driver = http_server
    event, _ = push_event(constants.NOTIFICATION_TOPIC_UPDATE)
    payload = event.notification_event()
    payload["event_params"]["priority"] = "high"
    response = requests.post(base + "/v1/events/", json=payload, timeout=5)
    assert response.status_code in (401, 403)
    headers = source_headers(driver, payload)
    response = requests.post(
        base + "/v1/events/", json=payload, headers=headers, timeout=5
    )
    assert response.status_code == 201, response.text
    stored = requests.get(
        base + "/v1/events/" + payload["uuid"], headers=headers, timeout=5
    )
    assert stored.status_code == 200, stored.text
    assert stored.json()["uuid"] == payload["uuid"]
    assert stored.json()["event_params"]["kind"] == constants.NOTIFICATION_TOPIC_UPDATE
    assert "devices" not in stored.json()["event_params"]


def test_deployed_source_role_can_submit_without_a_project_scope(http_server):
    base, driver = http_server
    event, _ = push_event()
    payload = event.notification_event()
    root = pathlib.Path(__file__).parents[3] / "exordos/manifests"
    environment = jinja2.Environment(loader=jinja2.FileSystemLoader(root))
    manifest = yaml.safe_load(
        environment.get_template("push_source.yaml.j2").render(
            organization_uuid=payload["event_params"]["organization_uuid"]
        )
    )
    core_catalog = yaml.safe_load(
        environment.get_template("exordos_notification.yaml.j2").render(
            version="1.2.8-dev",
            images={"exordos_notification": "https://example.com/image"},
        )
    )
    permissions = {
        name: spec["name"]
        for name, spec in manifest["resources"]["$core.iam.permissions"].items()
    }
    for name, spec in manifest.get("imports", {}).items():
        key = spec["link"].rsplit("$", 1)[1]
        permissions["imports." + name] = core_catalog["resources"][
            "$core.iam.permissions"
        ][key]["name"]
    granted = []
    for binding in manifest["resources"]["$core.iam.permissionbinding"].values():
        reference = binding["permission"]
        key = reference.rsplit("$", 1)[1].split(":")[0]
        granted.append(
            permissions[("imports." if ".imports." in reference else "") + key]
        )
    headers = driver.headers(granted, project_scoped=False)
    response = requests.post(
        base + "/v1/events/", json=payload, headers=headers, timeout=5
    )
    assert response.status_code == 201, response.text
    assert (
        requests.get(
            base + "/v1/events/" + payload["uuid"], headers=headers, timeout=5
        ).status_code
        == 200
    )


def test_push_requires_source_authorization_and_uuid_is_idempotent(http_server):
    base, driver = http_server
    event, _ = push_event()
    payload = event.notification_event()
    url = base + "/v1/events/"
    assert requests.post(url, json=payload, timeout=5).status_code in (401, 403)
    wrong = driver.headers([f"exordos_notification.source_{uuid.uuid4()}.*"])
    assert requests.post(url, json=payload, headers=wrong, timeout=5).status_code == 403
    headers = source_headers(driver, payload)
    response = requests.post(url, json=payload, headers=headers, timeout=5)
    assert response.status_code == 201, response.text
    assert (
        requests.post(url, json=payload, headers=headers, timeout=5).status_code == 409
    )
    resource = url + payload["uuid"]
    assert requests.get(resource, timeout=5).status_code in (401, 403)
    assert requests.get(resource, headers=wrong, timeout=5).status_code == 403
    assert (
        requests.get(resource, headers=headers, timeout=5).json()["uuid"]
        == payload["uuid"]
    )


def test_mobile_api_responses_omit_device_addresses_without_changing_the_snapshot(
    http_server,
):
    base, driver = http_server
    event, _ = push_event()
    payload = event.notification_event()
    headers = source_headers(driver, payload)
    created = requests.post(
        base + "/v1/events/", json=payload, headers=headers, timeout=5
    )
    assert created.status_code == 201
    read = requests.get(
        base + "/v1/events/" + payload["uuid"], headers=headers, timeout=5
    )
    assert read.status_code == 200
    for response in [created, read]:
        assert "devices" not in response.json()["event_params"]
        for device in payload["event_params"]["devices"]:
            assert device["registration_token"] not in response.text
    saved = models.Event.objects.get_one(filters={"uuid": event.uuid})
    assert saved.event_params == event.event_params


def test_push_rejects_project_mismatch_and_cannot_be_rewritten(http_server):
    base, driver = http_server
    event, _ = push_event()
    payload = event.notification_event()
    headers = source_headers(driver, payload)
    payload["event_params"] = {
        **payload["event_params"],
        "project_uuid": str(uuid.uuid4()),
    }
    assert (
        requests.post(
            base + "/v1/events/", json=payload, headers=headers, timeout=5
        ).status_code
        == 400
    )
    payload = event.notification_event()
    assert (
        requests.post(
            base + "/v1/events/", json=payload, headers=headers, timeout=5
        ).status_code
        == 201
    )
    resource = base + "/v1/events/" + payload["uuid"]
    assert (
        requests.put(
            resource, json={"event_params": {}}, headers=headers, timeout=5
        ).status_code
        == 403
    )
    assert requests.delete(resource, headers=headers, timeout=5).status_code == 403


def test_anonymous_collection_does_not_expose_push_events(http_server):
    base, _ = http_server
    assert requests.get(base + "/v1/events/", timeout=5).status_code in (401, 403)


def test_legacy_event_cannot_be_promoted_to_push_without_authorization(http_server):
    base, _ = http_server
    push, _ = push_event()
    event_type = models.EventType(project_id=push.project_id)
    event_type.insert()
    event = models.Event(
        project_id=push.project_id, event_type=event_type, event_params={}
    )
    event.insert()
    payload = push.notification_event()
    response = requests.put(
        base + f"/v1/events/{event.uuid}",
        json={
            "event_type": payload["event_type"],
            "event_params": payload["event_params"],
            "exchange": payload["exchange"],
        },
        timeout=5,
    )
    assert response.status_code in (401, 403)


def test_mobile_event_rejects_accidental_plaintext_fields(http_server):
    base, driver = http_server
    event, _ = push_event()
    payload = event.notification_event()
    payload["event_params"]["sender_name"] = "Must be encrypted"
    response = requests.post(
        base + "/v1/events/",
        json=payload,
        headers=source_headers(driver, payload),
        timeout=5,
    )
    assert response.status_code == 400, response.text


def test_duplicate_binding_does_not_duplicate_device_delivery(http_server):
    from unittest import mock

    from restalchemy.common import contexts

    base, driver = http_server
    event, template = push_event()
    template.is_default = True
    template.provider.insert()
    template.insert()
    user = driver.headers(
        user_uuid=event.exchange.user_uuid, project_uuid=event.project_id
    )
    selection = {
        "event_type": f"/v1/event_types/{event.event_type.uuid}",
        "template": f"/v1/templates/{template.uuid}",
    }
    for _ in range(2):
        assert (
            requests.post(
                base + "/v1/bindings/", headers=user, json=selection, timeout=5
            ).status_code
            == 201
        )
    payload = event.notification_event()
    result = requests.post(
        base + "/v1/events/",
        headers=source_headers(driver, payload),
        json=payload,
        timeout=5,
    )
    assert result.status_code == 201, result.text
    with contexts.Context().session_manager():
        pending = models.UnprocessedEvent.objects.get_one(filters={"event": event})
        pending.process_event(iam_client=mock.Mock())
    rendered = models.RenderedEvent.objects.get_all(filters={"event_id": event.uuid})
    assert len(rendered) == len(payload["event_params"]["devices"])


def test_user_exchange_preserves_both_v1_response_keys(http_server):
    base, driver = http_server
    event, _ = push_event()
    payload = event.notification_event()
    recipient = payload["exchange"].pop("user_uuid")
    payload["exchange"]["user_id"] = recipient
    headers = source_headers(driver, payload)
    response = requests.post(
        base + "/v1/events/", json=payload, headers=headers, timeout=5
    )
    assert response.status_code == 201, response.text
    read = requests.get(
        base + "/v1/events/" + payload["uuid"], headers=headers, timeout=5
    )
    assert read.status_code == 200, read.text
    for result in [response, read]:
        assert result.json()["exchange"] == {
            "kind": "User",
            "user_uuid": recipient,
            "user_id": recipient,
        }


@pytest.mark.parametrize(
    "path,value",
    [
        (("devices",), []),
        (("devices",), None),
        (("devices", 0, "registration_token"), ""),
        (("devices", 0, "registration_token"), 123),
        (("devices", 0, "registration_token"), "x" * 4097),
        (("devices", 0, "device_uuid"), "ABCD0000-0000-0000-0000-000000000000"),
        (("devices", 0, "content", "algorithm"), "plaintext"),
        (("devices", 0, "content", "enc"), "YQ"),
        (("devices", 0, "content", "ciphertext"), "YQ"),
        (("devices", 0, "content", "ciphertext"), "!" * 24),
        (("devices", 0, "content", "key_uuid"), "invalid"),
        (("devices", 0, "content", "text"), "private plaintext"),
        (("devices", 0, "text"), "private plaintext"),
        (("kind",), "notification_message_unknown"),
        (("unexpected",), "private plaintext"),
    ],
)
def test_message_parameter_models_reject_malformed_requests(http_server, path, value):
    base, driver = http_server
    event, _ = push_event()
    payload = event.notification_event()
    target = payload["event_params"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    response = requests.post(
        base + "/v1/events/",
        json=payload,
        headers=source_headers(driver, payload),
        timeout=5,
    )
    assert response.status_code == 400, response.text


def test_empty_topic_zero_can_be_submitted_and_rendered(http_server):
    base, driver = http_server
    event, template = push_event(constants.NOTIFICATION_TOPIC_UPDATE)
    payload = event.notification_event()
    payload["event_params"]["message_uuid"] = None
    headers = source_headers(driver, payload)
    response = requests.post(
        base + "/v1/events/", json=payload, headers=headers, timeout=5
    )
    assert response.status_code == 201, response.text
    rendered = template.content.render(
        {
            "_notification_event": payload,
            "_notification_expires_at": event.notification_expires_at(),
        }
    )
    assert len(rendered) == 2
    assert all(item.payload["message_uuid"] is None for item in rendered)
