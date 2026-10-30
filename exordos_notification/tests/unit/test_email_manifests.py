# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").
import pathlib
import subprocess
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


def test_email_sender_uses_platform_managed_token_and_minimal_permissions():
    environment = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT))
    manifest = yaml.safe_load(
        environment.get_template("notification_email.yaml.j2").render(
            version="1.2.8-dev"
        )
    )
    resources = manifest["resources"]
    config = resources["$core.config.configs"]["core_events_conf"]
    assert config["mode"] == "0600"
    settings = config["body"]["content"]
    token_config = resources["$core.config.configs"]["core_events_token"]
    assert token_config["mode"] == "0600"
    assert (
        "exordos_api_token = {$core.iam.tokens.$core_event_sender:access_token}"
        in token_config["body"]["content"]
    )
    assert "exordos_api_token" not in settings
    assert "client_type = async" in settings
    assert "systemctl restart ec-gservice" in config["on_change"]["command"]
    assert resources["$core.iam.users"]["core_event_sender"]["type"] == "service"
    token = resources["$core.iam.tokens"]["core_event_sender"]
    assert token["auto_renew"] is True
    assert token["expiration_delta"] == 604800
    assert token["scope"] == ""
    assert "core_event_sender" not in resources["$core.secret.passwords"]
    assert {v["name"] for v in resources["$core.iam.permissions"].values()} == {
        "exordos_notification.event_type_e0fe8070-e59c-4347-8df1-b3f638c06565.create",
        "exordos_notification.event_type_e0fe8070-e59c-4347-8df1-b3f638c06565.read",
    }


@pytest.mark.parametrize(
    "missing", [None, "core_event_type_mapping", "core_events_conf"]
)
@pytest.mark.parametrize("restart_code", [0, 7])
def test_email_token_rotation_restarts_sender_without_interrupting_core_api(
    tmp_path, missing, restart_code
):
    manifest = yaml.safe_load(
        jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT))
        .get_template("notification_email.yaml.j2")
        .render(version="1.2.11-dev")
    )
    configs = manifest["resources"]["$core.config.configs"]
    for name, config in configs.items():
        if name == missing:
            continue
        path = tmp_path / config["path"].lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config["body"]["content"])
    calls = tmp_path / "systemctl-calls"
    systemctl = tmp_path / "systemctl"
    systemctl.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{calls}"\nexit {restart_code}\n'
    )
    systemctl.chmod(0o700)
    rotating = [
        config
        for config in configs.values()
        if "exordos_api_token =" in config["body"]["content"]
    ]
    assert len(rotating) == 1
    command = rotating[0]["on_change"]["command"]
    command = command.replace("/etc/exordos_core/", f"{tmp_path}/etc/exordos_core/")
    result = subprocess.run(
        ["/bin/sh", "-ec", command],
        env={"PATH": str(tmp_path)},
        check=False,
    )
    actual = calls.read_text().splitlines() if calls.exists() else []
    assert actual == ([] if missing else ["restart ec-gservice"])
    assert result.returncode == (0 if missing else restart_code)


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
