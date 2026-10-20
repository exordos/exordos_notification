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


@pytest.mark.parametrize("filename", ["mobile_push.yaml.j2", "push_source.yaml.j2"])
def test_dependencies_use_core_versions_instead_of_pep440(filename):
    source = (
        jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT))
        .get_template(filename)
        .render(
            protocol_kind="firebase",
            organization_uuid=str(uuid.uuid4()),
            firebase_project_id="synthetic-project",
        )
    )
    manifest = yaml.safe_load(source)
    for requirement in manifest["requirements"].values():
        # Core accepts X.Y.Z[-suffix], while packaging.Version also accepts .dev0.
        assert re.fullmatch(r"\d+\.\d+\.\d+(?:-.+)?", requirement["from_version"])


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


@pytest.mark.parametrize("enable_email", [True, False, "false", "default"])
def test_push_only_installation_omits_mail_dependency_without_changing_email_default(
    enable_email,
):
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(ROOT), undefined=jinja2.StrictUndefined
    )
    manifest = yaml.safe_load(
        environment.get_template("exordos_notification.yaml.j2").render(
            version="1.2.8-dev+contract",
            images={
                "exordos_notification": "https://images.example.com/notification.raw.zst"
            },
            **({} if enable_email == "default" else {"enable_email": enable_email}),
        )
    )
    resources = manifest["resources"]
    if enable_email is True or enable_email == "default":
        assert "mailaas" in manifest["requirements"]
        assert (
            "smtp_via_mailaas"
            in resources["$exordos_notification.types.email.providers"]
        )
        assert "core_events_conf" in resources["$core.config.configs"]
    else:
        assert "mailaas" not in yaml.safe_dump(manifest)
        assert "core_events_conf" not in resources["$core.config.configs"]
        assert "$exordos_notification.types.email.providers" not in resources
    assert "notification_cp" in resources["$core.compute.nodes"]
    assert "backend_node" in manifest["exports"]
