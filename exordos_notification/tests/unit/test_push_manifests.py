# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").
import ipaddress
import pathlib
import re
import uuid

import jinja2
import pytest
import yaml
from restalchemy.dm import types

from exordos_notification.common import constants
from exordos_notification.dm import models

ROOT = pathlib.Path(__file__).parents[3] / "exordos/manifests"


def test_push_runbook_uses_placeholders_for_private_deployment_addresses():
    source = (ROOT.parents[1] / "docs/mobile-push.md").read_text()
    private_count = 0
    for candidate in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", source):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if (
            address.is_private
            and not address.is_loopback
            and not address.is_unspecified
        ):
            private_count += 1
    assert private_count == 0, (
        "Keep assigned deployment addresses in private test records"
    )


def test_source_account_email_passes_the_core_iam_field_validator():
    source = (
        jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT))
        .get_template("push_source.yaml.j2")
        .render(organization_uuid=str(uuid.uuid4()))
    )
    account = yaml.safe_load(source)["resources"]["$core.iam.users"]["source"]
    assert types.Email(max_length=128).validate(account["email"])


@pytest.mark.parametrize(
    "filename",
    [
        "notification_firebase.yaml.j2",
        "notification_relay.yaml.j2",
        "push_source.yaml.j2",
    ],
)
def test_dependencies_use_core_versions_instead_of_pep440(filename):
    source = (
        jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT))
        .get_template(filename)
        .render(
            organization_uuid=str(uuid.uuid4()),
            firebase_project_id="synthetic-project",
        )
    )
    manifest = yaml.safe_load(source)
    for requirement in manifest["requirements"].values():
        # Core accepts X.Y.Z[-suffix], while packaging.Version also accepts .dev0.
        assert re.fullmatch(r"\d+\.\d+\.\d+(?:-.+)?", requirement["from_version"])


@pytest.mark.parametrize(
    "role,filename",
    [
        ("firebase", "notification_firebase.yaml.j2"),
        ("notification", "notification_relay.yaml.j2"),
    ],
)
def test_role_manifest_renders_real_protocol_and_template_models(role, filename):
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(ROOT), undefined=jinja2.StrictUndefined
    )
    source = str(uuid.uuid4())
    manifest = yaml.safe_load(
        environment.get_template(filename).render(
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
    assert "protocol_kind" not in (ROOT / filename).read_text()
    if role == "notification":
        assert "firebase" not in yaml.safe_dump(manifest).lower()
    else:
        assert "mobile-push-auth.json" not in yaml.safe_dump(manifest)


@pytest.mark.parametrize("transport", ["notification_relay", "notification_firebase"])
def test_transport_generates_and_exports_identity_without_manual_uuid(transport):
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(ROOT), undefined=jinja2.StrictUndefined
    )
    manifest = yaml.safe_load(
        environment.get_template(transport + ".yaml.j2").render(
            firebase_project_id="synthetic-project",
            notification_endpoint="https://notification.example.com",
        )
    )
    resources = manifest["resources"]
    assert resources["$core.vs.values"]["organization_uuid"]["value"] == "$self:uuid"
    assert (
        manifest["exports"]["organization_uuid"]["link"]
        == "$core.vs.variables.$organization_uuid"
    )
    content = resources["$core.config.configs"]["source_identity"]["body"]["content"]
    assert (
        "local_organization_uuid = {$core.vs.variables.$organization_uuid:value}"
        in content
    )
    source = yaml.safe_load(
        environment.get_template("push_source.yaml.j2").render(
            notification_transport=transport
        )
    )
    assert source["name"] == "notification_push_source"
    assert source["imports"]["organization_uuid"]["element"] == "$" + transport
    assert transport in source["requirements"]
    assert (
        source["resources"]["$core.iam.permissions"]["source_create"]["name"]
        == 'f"exordos_notification.source_{$notification_push_source.imports.$organization_uuid:value}.create"'
    )


def test_cli_can_discover_raw_site_templates_before_jinja_rendering():
    # The CLI selects elements by parsing raw YAML before building a template.
    for name in (
        "notification_relay",
        "notification_firebase",
        "push_source",
    ):
        raw = yaml.safe_load((ROOT / (name + ".yaml.j2")).read_text())
        assert raw["name"]
