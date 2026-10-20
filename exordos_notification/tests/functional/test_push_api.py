#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License.

import pathlib
import uuid

import jinja2
import requests
import yaml

from exordos_notification.dm import models
from exordos_notification.tests.unit.test_push_transports import push_event


def source_headers(driver, payload):
    source = payload["event_params"]["organization_uuid"]
    return driver.headers(
        [
            "exordos_notification.event_type.read",
            f"exordos_notification.source_{source}.create",
            f"exordos_notification.source_{source}.read",
        ]
    )


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
            enable_email=False,
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


def test_legacy_email_event_post_and_poll_remain_compatible(http_server):
    base, _ = http_server
    event_type = models.EventType(project_id=uuid.uuid4())
    event_type.insert()
    payload = {
        "uuid": str(uuid.uuid4()),
        "project_id": str(event_type.project_id),
        "event_type": f"/v1/event_types/{event_type.uuid}",
        "event_params": {"content": "legacy"},
        "exchange": {"kind": "Dummy"},
    }
    response = requests.post(
        base + "/v1/events/",
        json=payload,
        headers={"Authorization": "Bearer legacy-placeholder"},
        timeout=5,
    )
    assert response.status_code == 201, response.text
    assert (
        requests.get(base + "/v1/events/" + payload["uuid"], timeout=5).status_code
        == 200
    )


def test_anonymous_clients_cannot_read_provider_configuration(http_server):
    base, _ = http_server
    assert requests.get(base + "/v1/providers/", timeout=5).status_code in (401, 403)


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


def test_binding_api_replaces_transport_and_hides_other_users(http_server):
    base, driver = http_server
    project, owner, stranger = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    event_type = models.EventType(project_id=project)
    provider = models.Provider(
        project_id=project,
        name="Test",
        protocol=models.SimpleSmtpProtocol(
            host="smtp.example.com", port=25, noreply_email_address="sender@example.com"
        ),
    )
    event_type.insert()
    provider.insert()
    templates = []
    for name in ("First", "Second"):
        template = models.Template(
            project_id=project,
            name=name,
            params={},
            event_type=event_type,
            provider=provider,
            content=models.EmailContent(title=name),
        )
        template.insert()
        templates.append(template)
    permissions = [
        "exordos_notification.template.read",
        "exordos_notification.event_type.read",
    ]
    headers = driver.headers(permissions, user_uuid=owner, project_uuid=project)
    other = driver.headers(permissions, user_uuid=stranger, project_uuid=project)
    response = requests.post(
        base + "/v1/bindings/",
        headers=headers,
        json={
            "user": str(stranger),
            "project_id": str(uuid.uuid4()),
            "template": f"/v1/templates/{templates[0].uuid}",
            "event_type": f"/v1/event_types/{event_type.uuid}",
        },
        timeout=5,
    )
    assert response.status_code == 201, response.text
    binding = response.json()
    assert binding["user"] == str(owner)
    assert binding["project_id"] == str(project)
    resource = base + "/v1/bindings/" + binding["uuid"]
    assert requests.get(resource, headers=other, timeout=5).status_code == 404
    assert requests.get(base + "/v1/bindings/", headers=other, timeout=5).json() == []
    changed = requests.put(
        resource,
        headers=headers,
        json={"template": f"/v1/templates/{templates[1].uuid}"},
        timeout=5,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["template"].endswith(str(templates[1].uuid))
    assert requests.delete(resource, headers=other, timeout=5).status_code == 404
    assert requests.delete(resource, headers=headers, timeout=5).status_code == 204


def test_ordinary_user_can_choose_published_transport_without_operator_permissions(
    http_server,
):
    base, driver = http_server
    event, template = push_event()
    template.is_default = True
    template.provider.insert()
    template.insert()
    headers = driver.headers(
        project_uuid=event.project_id, user_uuid=event.exchange.user_uuid
    )
    response = requests.get(base + "/v1/templates/", headers=headers, timeout=5)
    assert response.status_code == 200, response.text
    assert str(template.uuid) in {row["uuid"] for row in response.json()}
    chosen = requests.post(
        base + "/v1/bindings/",
        headers=headers,
        timeout=5,
        json={
            "event_type": f"/v1/event_types/{event.event_type.uuid}",
            "template": f"/v1/templates/{template.uuid}",
        },
    )
    assert chosen.status_code == 201, chosen.text


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
    # The existing internal Core client and its legacy placeholder remain valid.
    internal = requests.post(base + "/v1/events/", json=payload, timeout=5)
    assert internal.status_code == 201, internal.text
    assert (
        requests.get(
            base + "/v1/events/" + internal.json()["uuid"],
            headers={"X-Notification-Relay": "1"},
            timeout=5,
        ).status_code
        == 403
    )
