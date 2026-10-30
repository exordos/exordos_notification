# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").
import pathlib
import uuid

import jinja2
import pytest
import yaml

ROOT = pathlib.Path(__file__).parents[3] / "exordos/manifests"


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
    assert "$core.config.configs" not in email["resources"]
    assert "notification_mail" in email["resources"]["$mailaas.types.mail.instances"]
    assert "$exordos_notification.types.email.event_types" not in email["resources"]
    assert "$exordos_notification.types.email.templates" not in email["resources"]
    assert "$core.iam.users" not in email["resources"]
    assert "$core.iam.tokens" not in email["resources"]
    assert set(email["imports"]) == {"exim4_1"}
    for filename in ("site_config.yaml.j2", "notification_site_config_example.yaml.j2"):
        site = yaml.safe_load(environment.get_template(filename).render(**values))
        assert set(site["requirements"]) == {"notification_email"}
        assert set(site["resources"]) == {"$core.vs.values"}
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


def test_managed_iam_settings_override_legacy_generated_config(tmp_path):
    from oslo_config import cfg

    environment = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT))
    manifest = yaml.safe_load(
        environment.get_template("exordos_notification.yaml.j2").render(
            version="1.2.11-dev",
            images={"exordos_notification": "https://images.example.com/image"},
        )
    )
    resource = manifest["resources"]["$core.config.configs"]["notification_iam_config"]
    assert (
        resource["path"]
        == "/etc/exordos_notification/exordos_notification.conf.d/95-iam.conf"
    )
    assert "exordos-notification-user-api" in resource["on_change"]["command"]
    content = resource["body"]["content"].removeprefix('f"\n').removesuffix('"\n')
    client_uuid = str(uuid.uuid4())
    for reference, value in {
        "{$exordos_notification.imports.$core_local_domain:name}": "example.com",
        "{$exordos_notification.imports.$var_iam_default_client_uuid:value}": client_uuid,
        "{$exordos_notification.imports.$var_iam_default_client_id:value}": "test-client",
    }.items():
        content = content.replace(reference, value)
    legacy = tmp_path / "legacy.conf"
    legacy.write_text(
        "[iam]\niam_endpoint = http://core.example.com/api/core/v1/iam/clients/\naudience = legacy\n"
    )
    directory = tmp_path / "dropins"
    directory.mkdir()
    (directory / "95-iam.conf").write_text(content)
    conf = cfg.ConfigOpts()
    conf.register_opts([cfg.StrOpt("iam_endpoint"), cfg.StrOpt("audience")], "iam")
    conf(["--config-file", str(legacy), "--config-dir", str(directory)])
    assert (
        conf.iam.iam_endpoint
        == "http://core.example.com/api/core/v1/iam/clients/" + client_uuid
    )
    assert conf.iam.audience == "test-client"
