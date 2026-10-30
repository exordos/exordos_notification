# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").
import pathlib
import shlex

import yaml


def test_release_publishes_annotation_from_verified_tag():
    root = pathlib.Path(__file__).parents[3]
    workflow = yaml.safe_load(
        (root / ".github/workflows/publish-to-pypi.yml").read_text()
    )
    steps = workflow["jobs"]["github-release"]["steps"]
    release_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Create GitHub Release"
    )
    checkout = next(
        step
        for step in steps[:release_index]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["fetch-depth"] == 0
    command = shlex.split(steps[release_index]["run"])
    assert "--notes-from-tag" in command
    assert "--verify-tag" in command
    assert "--notes" not in command
    assert "--repo" not in command
