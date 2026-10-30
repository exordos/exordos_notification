#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License.

import uuid

import pytest
import requests

from exordos_notification.dm import models


@pytest.mark.parametrize("resource", ["providers", "templates", "event_types"])
def test_authenticated_management_catalog_keeps_marker_pagination(
    http_server, resource
):
    base, driver = http_server
    project = uuid.uuid4()
    provider = models.Provider(
        project_id=project,
        name="Pagination",
        protocol=models.SimpleSmtpProtocol(
            host="smtp.example.com", port=25, noreply_email_address="sender@example.com"
        ),
    )
    provider.insert()
    event_type = models.EventType(project_id=project)
    event_type.insert()
    for name in ("First page", "Second page"):
        models.Template(
            project_id=project,
            name=name,
            params={},
            event_type=event_type,
            provider=provider,
            content=models.EmailContent(title=name),
        ).insert()
    permission = {
        "providers": "provider",
        "templates": "template",
        "event_types": "event_type",
    }[resource]
    headers = driver.headers(
        [f"exordos_notification.{permission}.read"], project_uuid=project
    )
    url = f"{base}/v1/{resource}/"
    first = requests.get(url, params={"page_limit": 1}, headers=headers, timeout=5)
    assert first.status_code == 200, first.text
    assert len(first.json()) == 1
    assert first.headers["X-Pagination-Limit"] == "1"
    marker = first.headers["X-Pagination-Marker"]
    assert marker == first.json()[0]["uuid"]
    second = requests.get(
        url, params={"page_limit": 1, "page_marker": marker}, headers=headers, timeout=5
    )
    assert second.status_code == 200, second.text
    assert all(row["uuid"] != marker for row in second.json())


def test_email_events_require_iam_and_event_type_permissions(http_server):
    base, driver = http_server
    event_type = models.EventType(project_id=uuid.uuid4())
    event_type.insert()
    payload = {
        "uuid": str(uuid.uuid4()),
        "project_id": str(event_type.project_id),
        "event_type": f"/v1/event_types/{event_type.uuid}",
        "event_params": {"content": "email confirmation"},
        "exchange": {"kind": "Dummy"},
    }
    url = base + "/v1/events/"
    for headers in ({}, {"Authorization": "Bearer legacy-placeholder"}):
        response = requests.post(url, json=payload, headers=headers, timeout=5)
        assert response.status_code in (401, 403), response.text
    wrong = driver.headers([])
    assert requests.post(url, json=payload, headers=wrong, timeout=5).status_code == 403
    headers = driver.headers(
        [
            f"exordos_notification.event_type_{event_type.uuid}.create",
            f"exordos_notification.event_type_{event_type.uuid}.read",
        ]
    )
    response = requests.post(url, json=payload, headers=headers, timeout=5)
    assert response.status_code == 201, response.text
    assert (
        requests.post(url, json=payload, headers=headers, timeout=5).status_code == 409
    )
    resource = url + payload["uuid"]
    assert requests.get(resource, timeout=5).status_code in (401, 403)
    assert requests.get(resource, headers=wrong, timeout=5).status_code == 403
    assert requests.get(resource, headers=headers, timeout=5).status_code == 200
    assert (
        requests.put(
            resource, json={"event_params": {}}, headers=headers, timeout=5
        ).status_code
        == 403
    )
    assert requests.delete(resource, headers=headers, timeout=5).status_code == 403


def test_anonymous_clients_cannot_read_provider_configuration(http_server):
    base, _ = http_server
    assert requests.get(base + "/v1/providers/", timeout=5).status_code in (401, 403)


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


def test_management_permissions_are_restricted_to_token_project(http_server):
    base, driver = http_server
    project = uuid.uuid4()
    headers = driver.headers(["exordos_notification.provider.*"], project_uuid=project)
    payload = {
        "project_id": str(uuid.uuid4()),
        "name": "Other project",
        "protocol": {
            "kind": "SimpleSMTP",
            "host": "smtp.example.com",
            "port": 25,
            "noreply_email_address": "sender@example.com",
        },
    }
    url = base + "/v1/providers/"
    assert (
        requests.post(url, json=payload, headers=headers, timeout=5).status_code == 403
    )
    payload["project_id"] = str(project)
    own = requests.post(url, json=payload, headers=headers, timeout=5)
    assert own.status_code == 201, own.text
    other = driver.headers(
        ["exordos_notification.provider.*"], project_uuid=uuid.uuid4()
    )
    resource = url + own.json()["uuid"]
    assert requests.get(resource, headers=other, timeout=5).status_code == 404
    assert (
        requests.put(
            resource, json={"name": "Changed"}, headers=other, timeout=5
        ).status_code
        == 404
    )
    assert requests.delete(resource, headers=other, timeout=5).status_code == 404
    rows = requests.get(url, headers=other, timeout=5)
    assert rows.status_code == 200
    assert own.json()["uuid"] not in [row["uuid"] for row in rows.json()]


def email_event():
    event_type = models.EventType(project_id=uuid.uuid4())
    event_type.insert()
    provider = models.Provider(
        name="Email",
        project_id=event_type.project_id,
        protocol=models.SimpleSmtpProtocol(
            host="smtp.example.com", port=25, noreply_email_address="sender@example.com"
        ),
    )
    event = models.Event(
        event_type=event_type,
        project_id=event_type.project_id,
        exchange=models.UserExchange(user_uuid=uuid.uuid4()),
        event_params={},
    )
    template = models.Template(
        name="Email",
        project_id=event_type.project_id,
        event_type=event_type,
        provider=provider,
        params={},
        content=models.EmailContent(),
    )
    return event, template


def test_ordinary_user_can_choose_published_transport_without_operator_permissions(
    http_server,
):
    base, driver = http_server
    event, template = email_event()
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


def test_binding_update_keeps_owner_and_project_but_allows_transport_selection(
    http_server,
):
    base, driver = http_server
    _, first = email_event()
    first.is_default = True
    first.provider.insert()
    first.insert()
    second = models.Template(
        name="Another transport",
        project_id=first.project_id,
        event_type=first.event_type,
        provider=first.provider,
        params={},
        content=models.EmailContent(),
        is_default=True,
    )
    second.insert()
    owner, project = uuid.uuid4(), uuid.uuid4()
    headers = driver.headers(user_uuid=owner, project_uuid=project)
    body = {
        "user": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "event_type": "/v1/event_types/" + str(first.event_type.uuid),
        "template": "/v1/templates/" + str(first.uuid),
    }
    created = requests.post(
        base + "/v1/bindings/", headers=headers, json=body, timeout=5
    )
    assert created.status_code == 201, created.text
    url = base + "/v1/bindings/" + created.json()["uuid"]
    other_owner, other_project = uuid.uuid4(), uuid.uuid4()
    changed = requests.put(
        url,
        headers=headers,
        json={
            "user": str(other_owner),
            "project_id": str(other_project),
            "template": "/v1/templates/" + str(second.uuid),
        },
        timeout=5,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["user"] == str(owner)
    assert changed.json()["project_id"] == str(project)
    assert changed.json()["template"] == "/v1/templates/" + str(second.uuid)
    hidden = requests.get(
        url,
        headers=driver.headers(user_uuid=other_owner, project_uuid=other_project),
        timeout=5,
    )
    assert hidden.status_code == 404, hidden.text


def test_user_exchange_preserves_both_v1_response_keys(http_server):
    base, driver = http_server
    event, _ = email_event()
    recipient = str(event.exchange.user_uuid)
    payload = {
        "uuid": str(event.uuid),
        "event_type": f"/v1/event_types/{event.event_type.uuid}",
        "project_id": str(event.project_id),
        "event_params": {},
        "exchange": {"kind": "User", "user_id": recipient},
    }
    headers = driver.headers(
        [
            f"exordos_notification.event_type_{event.event_type.uuid}.create",
            f"exordos_notification.event_type_{event.event_type.uuid}.read",
        ]
    )
    created = requests.post(
        base + "/v1/events/", json=payload, headers=headers, timeout=5
    )
    assert created.status_code == 201, created.text
    read = requests.get(
        base + "/v1/events/" + payload["uuid"], headers=headers, timeout=5
    )
    assert read.status_code == 200, read.text
    for response in (created, read):
        assert response.json()["exchange"] == {
            "kind": "User",
            "user_uuid": recipient,
            "user_id": recipient,
        }
