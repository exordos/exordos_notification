import os
import pathlib
import subprocess

import pytest


@pytest.mark.parametrize("stop_status", [0, 1])
def test_invalid_tls_replacement_stops_nginx(tmp_path, stop_status):
    site = tmp_path / "sites-available" / "exordos-notification.conf"
    site.parent.mkdir()
    site.touch()
    enabled = tmp_path / "sites-enabled" / site.name
    enabled.parent.mkdir()
    enabled.symlink_to(site)
    bundle = tmp_path / "notification-tls" / "api.pem"
    bundle.parent.mkdir()
    bundle.write_text("invalid replacement")

    tools = tmp_path / "bin"
    tools.mkdir()
    nginx = tools / "nginx"
    nginx.write_text("#!/bin/sh\nexit 1\n")
    nginx.chmod(0o755)
    systemctl = tools / "systemctl"
    calls = tmp_path / "systemctl.calls"
    systemctl.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" > "$SYSTEMCTL_CALLS"\n'
        'exit "$SYSTEMCTL_STATUS"\n'
    )
    systemctl.chmod(0o755)

    source = pathlib.Path(__file__).parents[3] / "exordos/images/configure-https.sh"
    helper = tmp_path / source.name
    helper.write_text(source.read_text().replace("/etc/nginx/", f"{tmp_path}/"))
    env = dict(
        os.environ,
        PATH=f"{tools}:{os.environ['PATH']}",
        SYSTEMCTL_CALLS=str(calls),
        SYSTEMCTL_STATUS=str(stop_status),
    )

    result = subprocess.run(["bash", str(helper)], env=env, check=False)

    assert result.returncode == 1
    assert enabled.is_symlink() == bool(stop_status)
    assert calls.read_text().strip() == "stop nginx.service"
