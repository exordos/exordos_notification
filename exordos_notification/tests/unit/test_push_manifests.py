# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").
import pathlib
import uuid

import jinja2
import pytest
import yaml

from exordos_notification.common import constants
from exordos_notification.dm import models

ROOT = pathlib.Path(__file__).parents[3] / "exordos/manifests"


@pytest.mark.parametrize("role", ["firebase", "notification"])
def test_role_manifest_renders_real_protocol_and_template_models(role):
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(ROOT), undefined=jinja2.StrictUndefined
    )
    source = str(uuid.uuid4())
    manifest = yaml.safe_load(
        environment.get_template("mobile_push.yaml.j2").render(
            protocol_kind=role,
            organization_uuid=source,
            firebase_project_id="synthetic-project",
            notification_endpoint="https://notification.example.com",
        )
    )
    resources = manifest["resources"]
    protocol = resources["$exordos_notification.types.email.providers"]["mobile_push"][
        "protocol"
    ]
    provider_class = (
        models.FirebaseProtocol if role == "firebase" else models.NotificationProtocol
    )
    assert provider_class(**protocol).kind == role
    event_types = resources["$exordos_notification.types.email.event_types"]
    assert {
        kind: uuid.UUID(value["uuid"]) for kind, value in event_types.items()
    } == constants.PUSH_EVENT_TYPES
    for template in resources["$exordos_notification.types.email.templates"].values():
        assert template["content"] == {
            "kind": "mobile_push" if role == "firebase" else "notification"
        }
        assert template["is_default"]
    assert "private_key" not in yaml.safe_dump(manifest)
