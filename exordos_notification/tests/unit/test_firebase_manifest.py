# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").

import pathlib

import jinja2
import yaml

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


def test_cli_can_discover_firebase_before_jinja_rendering():
    raw = yaml.safe_load((ROOT / "notification_firebase.yaml.j2").read_text())
    assert raw["name"] == "notification_firebase"


def test_firebase_credentials_are_not_manifest_parameters():
    source = (ROOT / "notification_firebase.yaml.j2").read_text()
    manifest = yaml.safe_dump(render("notification_firebase.yaml.j2"))
    assert "credentials_file" not in source
    assert "service_account" not in source
    assert "private_key" not in manifest
