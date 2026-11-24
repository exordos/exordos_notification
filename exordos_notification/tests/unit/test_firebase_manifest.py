# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").

import pathlib

import jinja2
import yaml

from exordos_notification.common import transports
from exordos_notification.dm import models

ROOT = pathlib.Path(__file__).parents[3] / "exordos/manifests"


def render(name):
    return yaml.safe_load(
        jinja2.Environment(
            loader=jinja2.FileSystemLoader(ROOT),
            undefined=jinja2.StrictUndefined,
        )
        .get_template(name)
        .render(
            version="2.0.3-dev+test",
            images={
                "exordos_notification": "https://images.example.com/notification.raw.zst"
            },
        )
    )


def test_firebase_is_an_independent_transport_element():
    base = render("exordos_notification.yaml.j2")
    assert "firebase" not in yaml.safe_dump(base).lower()

    firebase = render("notification_firebase.yaml.j2")
    assert set(firebase["requirements"]) == {"exordos_notification"}
    resources = firebase["resources"]
    assert set(resources) == {"$exordos_notification.types.email.providers"}
    assert "$core.iam.users" not in resources
    assert "$core.iam.tokens" not in resources
    assert "$exordos_notification.types.email.event_types" not in resources
    assert "$exordos_notification.types.email.templates" not in resources

    protocol = resources["$exordos_notification.types.email.providers"]["firebase"][
        "protocol"
    ]
    assert models.FirebaseProtocol(**protocol).ttl_seconds == 3600
    assert firebase["exports"]["firebase_provider"]["link"].endswith(
        "providers.$firebase"
    )


def test_firebase_element_is_built_and_tested_after_the_base_service():
    build = yaml.safe_load((ROOT.parent / "exordos.yaml").read_text())
    manifests = [item["manifest"] for item in build["build"]["elements"]]
    assert "manifests/notification_firebase.yaml.j2" in manifests

    workflow = yaml.safe_load(
        (ROOT.parents[1] / ".github/workflows/build.yml").read_text()
    )
    elements = workflow["jobs"]["Test"]["with"]["elements"].split()
    assert elements.index("exordos_notification") < elements.index(
        "notification_firebase"
    )


def test_cli_builds_both_optional_credential_elements():
    build = yaml.safe_load((ROOT.parent / "exordos.yaml").read_text())
    manifests = {item["manifest"] for item in build["build"]["elements"]}
    for element in (
        "notification_firebase_secret",
        "notification_firebase_credentials",
    ):
        assert f"manifests/{element}.yaml.j2" in manifests
        assert (
            yaml.safe_load((ROOT / f"{element}.yaml.j2").read_text())["name"] == element
        )


def test_cli_can_discover_firebase_before_jinja_rendering():
    raw = yaml.safe_load((ROOT / "notification_firebase.yaml.j2").read_text())
    assert raw["name"] == "notification_firebase"


def test_firebase_credentials_are_not_manifest_parameters():
    source = (ROOT / "notification_firebase.yaml.j2").read_text()
    manifest = yaml.safe_dump(render("notification_firebase.yaml.j2"))
    assert "credentials_file" not in source
    assert "service_account" not in source
    assert "private_key" not in manifest


def test_firebase_secret_can_be_seeded_without_touching_the_existing_file():
    manifest = render("notification_firebase_secret.yaml.j2")

    assert manifest["resources"] == {
        "$core.secret.secrets": {
            "service_account": {
                "name": "notification-firebase-service-account",
                "project_id": "12345678-c625-4fee-81d5-f691897b8142",
                "constructor": {"kind": "plain"},
            }
        }
    }


def test_firebase_credentials_render_the_secret_into_the_worker_file():
    manifest = render("notification_firebase_credentials.yaml.j2")

    assert "notification_firebase_secret" in manifest["requirements"]
    secret = render("notification_firebase_secret.yaml.j2")
    service = render("exordos_notification.yaml.j2")
    for name, source, export in (
        ("service_account", secret, "service_account"),
        ("notification_node", service, "backend_node"),
    ):
        assert manifest["imports"][name] == {
            "element": f"${source['name']}",
            **source["exports"][export],
        }
    configs = manifest["resources"]["$core.config.configs"]
    config = configs["service_account"]
    assert config["path"] == transports.FIREBASE_CREDENTIALS_FILE
    assert (config["owner"], config["group"], config["mode"]) == (
        "root",
        "root",
        "0600",
    )
    assert config["target"] == {
        "kind": "node",
        "node": "$notification_firebase_credentials.imports.$notification_node:uuid",
    }
    assert config["body"] == {
        "kind": "text",
        "content": "$notification_firebase_credentials.imports.$service_account:value",
    }
    assert config["on_change"] == {
        "kind": "shell",
        "command": "systemctl try-restart exordos-notification-mail-agent.service",
    }
