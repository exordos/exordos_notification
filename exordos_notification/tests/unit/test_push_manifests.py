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


@pytest.mark.parametrize("trim_blocks", [False, True])
def test_service_and_email_are_independent_elements(trim_blocks):
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(ROOT),
        undefined=jinja2.StrictUndefined,
        trim_blocks=trim_blocks,
    )
    values = {
        "version": "1.2.8-dev+contract",
        "images": {
            "exordos_notification": "https://images.example.com/notification.raw.zst"
        },
    }
    manifest = yaml.safe_load(
        environment.get_template("exordos_notification.yaml.j2").render(**values)
    )
    resources = manifest["resources"]
    assert set(manifest["requirements"]) == {"core", "dbaas"}
    assert "mailaas" not in yaml.safe_dump(manifest)
    assert "core_events_conf" not in resources["$core.config.configs"]
    assert "$exordos_notification.types.email.providers" not in resources
    assert "notification_smtp_password" not in resources["$core.secret.passwords"]
    assert "notification_cp" in resources["$core.compute.nodes"]
    assert {"backend_node", "notification_cp", "notification_cluster_pg"} <= set(
        manifest["exports"]
    )
    assert (
        "core_lb_notification_route"
        in resources["$exordos_notification.imports.$core_lb_http_vhost.routes"]
    )
    email = yaml.safe_load(
        environment.get_template("notification_email.yaml.j2").render(**values)
    )
    assert set(email["requirements"]) == {"exordos_notification", "mailaas"}
    assert "$core.compute.nodes" not in email["resources"]
    assert "core_events_conf" in email["resources"]["$core.config.configs"]
    assert "notification_mail" in email["resources"]["$mailaas.types.mail.instances"]
    assert (
        email["resources"]["$exordos_notification.types.email.event_types"][
            "email_confirmation"
        ]["uuid"]
        == "e0fe8070-e59c-4347-8df1-b3f638c06565"
    )
    for filename in ("site_config.yaml.j2", "notification_site_config_example.yaml.j2"):
        site = yaml.safe_load(environment.get_template(filename).render(**values))
        assert set(site["requirements"]) == {"notification_email"}
        for imported in site["imports"].values():
            assert imported["element"] == "$notification_email"
            assert imported["link"] in {
                export["link"] for export in email["exports"].values()
            }
    build = yaml.safe_load((ROOT.parent / "exordos.yaml").read_text())
    assert {"manifest": "manifests/notification_email.yaml.j2"} in build["build"][
        "elements"
    ]
    assert "enable_email" not in (ROOT / "exordos_notification.yaml.j2").read_text()


def test_realm_acceptance_installs_email_before_its_site_configuration():
    workflow = yaml.safe_load(
        (ROOT.parents[1] / ".github/workflows/build.yml").read_text()
    )
    elements = workflow["jobs"]["Test"]["with"]["elements"].split()
    assert elements.index("exordos_notification") < elements.index("notification_email")
    assert elements.index("notification_email") < elements.index(
        "notification_site_config_example"
    )


def test_base_manifest_supports_cli_name_probe_before_jinja_rendering():
    # The all-elements CLI build probes Manifest.name through yaml.safe_load
    # before rendering Jinja. Selected-element builds can mask this failure.
    manifest = yaml.safe_load((ROOT / "exordos_notification.yaml.j2").read_text())
    assert manifest["name"] == "exordos_notification"
