import datetime
import json
import time
from unittest import mock
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import httpcore
import jwt
import pytest

from exordos_notification.common import apns
from exordos_notification.common import transports
from exordos_notification.dm import models


def _rendered(priority="high", environment="sandbox", payload=None):
    event = mock.Mock(
        uuid=uuid.uuid4(),
        last_retry_at=datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(minutes=10),
    )
    if payload is None:
        aps = {"content-available": 1}
        if priority == "high":
            aps["alert"] = {"loc-key": "NEW_ALERT"}
        payload = {"aps": aps, "data": {"ciphertext": "opaque"}}
    return models.ApnsContent().render(
        {
            "device_uuid": str(uuid.uuid4()),
            "registration_token": "ab" * 32,
            "payload": json.dumps(payload),
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
    models.ApnsProtocol(topic="com.example.app").send(content, {})
    call = connection.client.request.call_args
    assert call.args == (
        "POST",
        f"https://{host}/3/device/{content.registration_token}",
    )
    assert call.kwargs["headers"] == {
        "authorization": "bearer synthetic",
        "content-type": "application/json",
        "apns-topic": "com.example.app",
        "apns-push-type": push_type,
        "apns-priority": apns_priority,
        "apns-id": str(content.event_uuid),
        "apns-collapse-id": ("c" * 64 if priority == "high" else "bg-" + "c" * 61),
        "apns-expiration": str(int(content.expires_at.timestamp())),
    }
    assert json.loads(call.kwargs["content"]) == content.payload


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
        apns.send("com.example.app", content)
    assert error.value.errors == [
        {"device_uuid": str(content.device_uuid), "code": code}
    ]
    assert error.value.permanent is permanent
    assert "private" not in str(error.value)


def test_apns_payload_limit_prevents_send(connection):
    with pytest.raises(transports.DeliveryError, match="apns_payload_too_large"):
        apns.send(
            "com.example.app", _rendered(payload={"ciphertext": "x" * 4096})
        )
    connection.client.request.assert_not_called()


def test_apns_retries_when_credentials_are_temporarily_unavailable(monkeypatch):
    get_connection = mock.Mock(side_effect=FileNotFoundError)
    monkeypatch.setattr(apns, "connection", get_connection)
    content = _rendered()

    with pytest.raises(transports.DeliveryError, match="apns_credentials_error") as error:
        apns.send("com.example.app", content)
    assert not error.value.permanent

    session = mock.Mock()
    session.client.request.return_value = httpcore.Response(200)
    get_connection.side_effect = None
    get_connection.return_value = session
    apns.send("com.example.app", content)
    session.client.request.assert_called_once()


def test_apns_replaces_a_rejected_provider_token(monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    session = apns.Connection(
        {"team_id": "test-team", "key_id": "test-key", "private_key": key}
    )
    session.client = mock.Mock()
    expired = httpcore.Response(403, content=b'{"reason":"ExpiredProviderToken"}')
    expired.read()
    session.client.request.side_effect = [expired, httpcore.Response(200)]
    monkeypatch.setattr(apns, "connection", lambda _: session)
    content = _rendered()
    now = int(time.time())
    monkeypatch.setattr(apns.time, "time", mock.Mock(side_effect=[now, now + 5]))

    with pytest.raises(transports.DeliveryError, match="apns_http_403") as error:
        apns.send("com.example.app", content)
    assert not error.value.permanent
    apns.send("com.example.app", content)

    first, second = session.client.request.call_args_list
    assert first.kwargs["headers"]["authorization"] != second.kwargs["headers"][
        "authorization"
    ]


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
