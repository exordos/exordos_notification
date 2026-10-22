#    Copyright 2026 Genesis Corporation.
#
#    All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.


"""The public notification route exposes only authenticated management APIs."""

import pathlib
import re

import jinja2
import pytest
import yaml


@pytest.fixture
def manifest() -> dict:
    path = (
        pathlib.Path(__file__).parents[3]
        / "exordos/manifests/exordos_notification.yaml.j2"
    )
    return yaml.safe_load(
        jinja2.Template(path.read_text()).render(
            version="0.0.0",
            images={"exordos_notification": "test-image"},
        )
    )


def test_management_route_reaches_notification_api(manifest: dict) -> None:
    resources = manifest["resources"]
    pool = resources["$exordos_notification.imports.$core_lb.backend_pools"][
        "core_lb_notification_backend_http"
    ]
    assert pool["endpoints"] == [
        {
            "kind": "host",
            "host": "$core.compute.nodes.$notification_cp:default_network:ipv4",
            "port": 8080,
            "weight": 1,
        }
    ]
    route = resources["$exordos_notification.imports.$core_lb_http_vhost.routes"][
        "core_lb_notification_route"
    ]
    pattern = route["condition"]["value"]
    for collection in ("providers", "event_types", "templates"):
        for suffix in ("/", "/12345678-1234-4234-8234-123456789abc"):
            url = f"/api/notification/v1/{collection}{suffix}"
            assert re.fullmatch(pattern, url)
    for path in ("events/", "events/123", "providers_other/", "", "../events/"):
        assert not re.fullmatch(pattern, f"/api/notification/v1/{path}")
    rewrite = route["condition"]["modifiers"][-1]
    assert rewrite == {
        "kind": "rewrite_url",
        "regex": "^/api/notification/(.*)",
        "replacement": "/$1",
    }
    assert {
        "kind": "set_header",
        "name": "X-Forwarded-Prefix",
        "value": "/api/notification",
    } in route["condition"]["modifiers"]
    assert route["condition"]["actions"][0]["pool"].endswith(
        ".$core_lb_notification_backend_http:uuid"
    )
    assert manifest["imports"]["core_lb"]["element"] == "$core"
    assert manifest["imports"]["core_lb_http_vhost"]["element"] == "$core"
