import datetime
import json
import pathlib
import time
import uuid
from unittest import mock

import httpcore
import jinja2
import jwt
import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from exordos_notification.common import apns, transports
from exordos_notification.dm import models


def _rendered(priority="high", environment="sandbox", payload=None):
    event = mock.Mock(
        uuid=uuid.uuid4(),
        last_retry_at=datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(minutes=10),
    )
    return models.ApnsContent().render(
        {
            "device_uuid": str(uuid.uuid4()),
            "registration_token": "ab" * 32,
            "payload": json.dumps(
                {"content": {"ciphertext": "opaque"}} if payload is None else payload
            ),
            "apns_environment": environment,
            "apns_collapse_id": "c" * 64,
            "priority": priority,
        },
        event=event,
    )


@pytest.fixture
def connection(monkeypatch):
    connection = mock.Mock()
    connection.authorization.return_value = "bearer synthetic"
    connection.client.request.return_value = httpcore.Response(200)
    monkeypatch.setattr(apns, "connection", mock.Mock(return_value=connection))
    return connection


@pytest.mark.parametrize(
    "priority,environment,host,push_type,apns_priority",
    [
        ("high", "sandbox", "api.sandbox.push.apple.com", "alert", "10"),
        ("normal", "production", "api.push.apple.com", "background", "5"),
    ],
)
def test_apns_request(
    connection, priority, environment, host, push_type, apns_priority
):
    content = _rendered(priority, environment)
    models.ApnsProtocol(topic="com.exordos.workspace").send(content, {})
    call = connection.client.request.call_args
    assert call.args == (
        "POST",
        f"https://{host}/3/device/{content.registration_token}",
    )
    assert call.kwargs["headers"] == {
        "authorization": "bearer synthetic",
        "content-type": "application/json",
        "apns-topic": "com.exordos.workspace",
        "apns-push-type": push_type,
        "apns-priority": apns_priority,
        "apns-id": str(content.event_uuid),
        "apns-collapse-id": ("c" * 64 if priority == "high" else "bg-" + "c" * 61),
        "apns-expiration": str(int(content.expires_at.timestamp())),
    }
    payload = json.loads(call.kwargs["content"])
    assert payload["workspace"] == content.payload
    assert payload["aps"]["content-available"] == 1
    if priority == "high":
        assert payload["aps"]["alert"] == {"loc-key": "WORKSPACE_PUSH_NEW_MESSAGE"}
    else:
        assert "alert" not in payload["aps"]


@pytest.mark.parametrize(
    "status,reason,code,permanent",
    [
        (410, "Unregistered", "registration_token_inactive", True),
        (410, "ExpiredToken", "registration_token_inactive", True),
        (400, "BadDeviceToken", "apns_http_400", True),
        (403, "ExpiredProviderToken", "apns_http_403", False),
        (429, "TooManyRequests", "apns_http_429", False),
        (503, "Shutdown", "apns_http_503", False),
    ],
)
def test_apns_result(connection, status, reason, code, permanent):
    content = _rendered()
    response = httpcore.Response(
        status, content=json.dumps({"reason": reason, "token": "private"}).encode()
    )
    response.read()
    connection.client.request.return_value = response
    with pytest.raises(transports.DeliveryError) as error:
        apns.send("com.exordos.workspace", content)
    assert error.value.errors == [
        {"device_uuid": str(content.device_uuid), "code": code}
    ]
    assert error.value.permanent is permanent
    assert "private" not in str(error.value)


def test_apns_payload_limit_prevents_send(connection):
    with pytest.raises(transports.DeliveryError, match="apns_payload_too_large"):
        apns.send(
            "com.exordos.workspace", _rendered(payload={"ciphertext": "x" * 4096})
        )
    connection.client.request.assert_not_called()


def test_apns_jwt_reuse_and_refresh():
    now = int(time.time())
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    session = apns.Connection(
        {"team_id": "test-team", "key_id": "test-key", "private_key": pem}
    )
    token = session.authorization(now).split()[1]
    assert jwt.get_unverified_header(token)["kid"] == "test-key"
    assert jwt.decode(token, key.public_key(), algorithms=["ES256"]) == {
        "iss": "test-team",
        "iat": now,
    }
    assert session.authorization(now + apns.TOKEN_LIFETIME - 1) == f"bearer {token}"
    assert session.authorization(now + apns.TOKEN_LIFETIME) != f"bearer {token}"


def test_apns_secret_only_reaches_notification_node():
    manifest = (
        pathlib.Path(__file__).parents[3]
        / "exordos/manifests/notification_apns.yaml.j2"
    )
    rendered = yaml.safe_load(
        jinja2.Template(manifest.read_text()).render(version="2.0.4-dev")
    )
    resources = rendered["resources"]
    file = resources["$core.config.configs"]["apns_credentials"]
    assert file["path"] == apns.CREDENTIALS_FILE
    assert file["mode"] == "0600"
    assert (
        file["target"]["node"] == "$notification_apns.imports.$notification_node:uuid"
    )
    assert file["body"]["content"] == "$core.secret.secrets.$apns_credentials:value"
    assert resources["$exordos_notification.types.email.providers"]["apns"][
        "protocol"
    ] == {"kind": "apns", "topic": "com.exordos.workspace"}
