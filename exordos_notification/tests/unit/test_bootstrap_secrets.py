# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").
import pathlib
import subprocess


def test_bootstrap_does_not_trace_credentials_loaded_from_init_file(tmp_path):
    root = pathlib.Path(__file__).parents[3]
    script = (
        (root / "exordos/images/bootstrap.sh")
        .read_text()
        .split("CORE_AGENT_CONFIG=")[0]
    )
    init = tmp_path / "init"
    marker = "synthetic-bootstrap-secret-do-not-log"
    init.write_text(
        f"IAM_USER_PASS={marker}\nGC_PG_PASS={marker}\nGC_PG_ENDPOINTS=127.0.0.1\n"
    )
    library = tmp_path / "lib_bootstrap.sh"
    library.write_text("")
    script = script.replace(
        "/usr/local/lib/exordos/lib_bootstrap.sh", str(library)
    ).replace("/etc/exordos_init.txt", str(init))
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True
    )
    assert marker not in result.stdout + result.stderr
