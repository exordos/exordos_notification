# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").

import datetime
import json
import uuid
from unittest import mock

import pytest
import requests
from google.auth import exceptions as google_auth_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import service_account
from restalchemy.common import exceptions

from exordos_notification.common import transports
from exordos_notification.dm import models


def firebase_content(**overrides):
    values = {
        "device_uuid": "{{ device_uuid }}",
        "registration_token": "token-{{ suffix }}",
        "data": {"payload": "{{ encrypted_payload }}"},
        "priority": "{{ priority }}",
    }
    values.update(overrides)
    return models.FirebaseAndroidContent(**values)


def rendered_content():
    return firebase_content().render(
        {
            "device_uuid": "00000000-0000-0000-0000-000000000009",
            "suffix": "one",
            "encrypted_payload": '{"kind":"encrypted","ciphertext":"opaque"}',
            "priority": "high",
        }
    )


def test_firebase_template_renders_only_string_data():
    content = rendered_content()
    assert firebase_content().KIND == "firebase_android"
    assert content.KIND == "rendered_firebase_android"
    assert content.device_uuid == uuid.UUID("00000000-0000-0000-0000-000000000009")
    assert content.registration_token == "token-one"
    assert content.data == {"payload": '{"kind":"encrypted","ciphertext":"opaque"}'}
    assert content.priority == "high"

    with pytest.raises(exceptions.ValidationErrorException):
        firebase_content(data={"payload": {"ciphertext": "opaque"}}).render({})


def test_firebase_event_does_not_require_an_iam_user_lookup():
    project_id = uuid.uuid4()
    event_type = models.EventType(project_id=project_id, name="push")
    provider = models.Provider(
        project_id=project_id,
        name="firebase",
        protocol=models.FirebaseProtocol(),
    )
    template = models.Template(
        project_id=project_id,
        name="push",
        event_type=event_type,
        provider=provider,
        params={},
        content=firebase_content(),
    )
    event = models.Event(
        project_id=project_id,
        event_type=event_type,
        exchange=models.UserExchange(user_uuid=uuid.uuid4()),
        event_params={
            "device_uuid": "00000000-0000-0000-0000-000000000009",
            "suffix": "one",
            "encrypted_payload": '{"kind":"encrypted"}',
            "priority": "normal",
        },
    )
    iam = mock.Mock()

    with mock.patch.object(models.Event, "get_templates", return_value=[template]):
        assert event.get_context(iam) == {}
        rendered = event.render(iam)

    iam.get_user.assert_not_called()
    assert len(rendered) == 1
    assert rendered[0].user_context == {}
    assert rendered[0].content.registration_token == "token-one"


def test_firebase_protocol_sends_data_only_through_credential_project():
    protocol = models.FirebaseProtocol(ttl_seconds=1800)
    content = rendered_content()
    session = mock.Mock()
    session.post.return_value.status_code = 200

    with mock.patch.object(
        transports,
        "firebase_session",
        return_value=(session, "firebase-project"),
    ):
        protocol.send(content, {})

    call = session.post.call_args
    assert (
        call.args[0]
        == "https://fcm.googleapis.com/v1/projects/firebase-project/messages:send"
    )
    assert call.kwargs["timeout"] == 10
    message = call.kwargs["json"]["message"]
    assert message["token"] == "token-one"
    assert message["data"] == content.data
    assert "token-one" not in json.dumps(message["data"])
    assert "notification" not in message
    assert message["android"] == {"priority": "high", "ttl": "1800s"}


def test_firebase_protocol_rejects_oversized_data_before_authentication():
    protocol = models.FirebaseProtocol()
    content = rendered_content()
    content.data = {"payload": "x" * 4097}

    with (
        mock.patch.object(transports, "firebase_session") as session,
        pytest.raises(RuntimeError, match="firebase_payload_too_large"),
    ):
        protocol.send(content, {})

    session.assert_not_called()


def test_firebase_provider_rejects_oversized_data_before_authentication():
    provider = models.Provider(
        project_id=uuid.uuid4(),
        name="firebase",
        protocol=models.FirebaseProtocol(),
    )
    content = rendered_content()
    content.data = {"payload": "x" * 4097}

    with (
        mock.patch.object(transports, "firebase_session") as session,
        pytest.raises(transports.DeliveryError) as raised,
    ):
        provider.send(content, {})

    assert raised.value.code == "firebase_payload_too_large"
    assert raised.value.permanent is True
    session.assert_not_called()


def test_firebase_protocol_reports_unregistered_without_exposing_response():
    protocol = models.FirebaseProtocol()
    content = rendered_content()
    session = mock.Mock()
    response = session.post.return_value
    response.status_code = 404
    response.json.return_value = {
        "error": {
            "details": [
                {
                    "@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError",
                    "errorCode": "UNREGISTERED",
                }
            ]
        }
    }

    with (
        mock.patch.object(
            transports,
            "firebase_session",
            return_value=(session, "firebase-project"),
        ),
        pytest.raises(transports.DeliveryError) as raised,
    ):
        protocol.send(content, {})

    assert raised.value.code == "registration_token_inactive"
    assert raised.value.permanent is True
    assert raised.value.errors == [
        {
            "device_uuid": "00000000-0000-0000-0000-000000000009",
            "code": "registration_token_inactive",
        }
    ]


def test_firebase_protocol_sanitizes_other_provider_errors():
    protocol = models.FirebaseProtocol()
    content = rendered_content()
    session = mock.Mock()
    response = session.post.return_value
    response.status_code = 503
    response.json.side_effect = ValueError("private response")

    with (
        mock.patch.object(
            transports,
            "firebase_session",
            return_value=(session, "firebase-project"),
        ),
        pytest.raises(transports.DeliveryError) as raised,
    ):
        protocol.send(content, {})

    assert raised.value.code == "firebase_http_503"
    assert raised.value.permanent is False
    assert raised.value.retry_after is None


def test_firebase_protocol_stops_retrying_permanent_request_errors():
    protocol = models.FirebaseProtocol()
    content = rendered_content()
    session = mock.Mock()
    response = session.post.return_value
    response.status_code = 400
    response.headers = {}
    response.json.return_value = {"error": {"status": "INVALID_ARGUMENT"}}

    with (
        mock.patch.object(
            transports,
            "firebase_session",
            return_value=(session, "firebase-project"),
        ),
        pytest.raises(transports.DeliveryError) as raised,
    ):
        protocol.send(content, {})

    assert raised.value.code == "firebase_http_400"
    assert raised.value.permanent is True


def test_firebase_protocol_reports_network_failure_without_exception_text():
    protocol = models.FirebaseProtocol()
    content = rendered_content()
    session = mock.Mock()
    session.post.side_effect = requests.ConnectionError("private endpoint")

    with (
        mock.patch.object(
            transports,
            "firebase_session",
            return_value=(session, "firebase-project"),
        ),
        pytest.raises(transports.DeliveryError) as raised,
    ):
        protocol.send(content, {})

    assert str(raised.value) == "firebase_transport_error"
    assert "private endpoint" not in str(raised.value)
    assert raised.value.permanent is False
    assert raised.value.errors == [
        {
            "device_uuid": "00000000-0000-0000-0000-000000000009",
            "code": "firebase_transport_error",
        }
    ]


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("private credential path"),
        google_auth_exceptions.GoogleAuthError("private session setup"),
    ],
)
def test_firebase_provider_normalizes_session_failures(failure):
    content = rendered_content()
    provider = models.Provider(
        project_id=uuid.uuid4(),
        name="firebase",
        protocol=models.FirebaseProtocol(),
    )

    with (
        mock.patch.object(transports, "firebase_session", side_effect=failure),
        pytest.raises(transports.DeliveryError) as raised,
    ):
        provider.send(content, {})

    assert str(raised.value) == "firebase_credentials_error"
    assert "private" not in str(raised.value)
    assert raised.value.permanent is True
    assert raised.value.errors == [
        {
            "device_uuid": "00000000-0000-0000-0000-000000000009",
            "code": "firebase_credentials_error",
        }
    ]


@pytest.mark.parametrize(
    "retryable,permanent",
    [(False, True), (True, False)],
)
def test_firebase_protocol_normalizes_refresh_failures(retryable, permanent):
    protocol = models.FirebaseProtocol()
    content = rendered_content()
    session = mock.Mock()
    session.post.side_effect = google_auth_exceptions.RefreshError(
        "private refresh",
        retryable=retryable,
    )

    with (
        mock.patch.object(
            transports,
            "firebase_session",
            return_value=(session, "firebase-project"),
        ),
        pytest.raises(transports.DeliveryError) as raised,
    ):
        protocol.send(content, {})

    assert str(raised.value) == "firebase_auth_error"
    assert "private refresh" not in str(raised.value)
    assert raised.value.permanent is permanent
    assert raised.value.errors == [
        {
            "device_uuid": "00000000-0000-0000-0000-000000000009",
            "code": "firebase_auth_error",
        }
    ]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("17", 17),
        ("Thu, 01 Jan 2026 00:00:17 GMT", 17),
        ("invalid", None),
    ],
)
def test_retry_after_supports_seconds_and_http_dates(value, expected):
    response = mock.Mock(headers={"Retry-After": value})
    now = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    assert transports.retry_after_seconds(response, now=now) == expected


def test_firebase_throttling_carries_retry_after():
    protocol = models.FirebaseProtocol()
    content = rendered_content()
    session = mock.Mock()
    response = session.post.return_value
    response.status_code = 429
    response.headers = {"Retry-After": "23"}
    response.json.return_value = {"error": {}}

    with (
        mock.patch.object(
            transports,
            "firebase_session",
            return_value=(session, "firebase-project"),
        ),
        pytest.raises(transports.DeliveryError) as raised,
    ):
        protocol.send(content, {})

    assert raised.value.code == "firebase_http_429"
    assert raised.value.retry_after == 23


def test_rendered_event_uses_backoff_and_stops_permanent_retries():
    content = rendered_content()
    provider = models.Provider(
        project_id=uuid.uuid4(),
        name="firebase",
        protocol=models.FirebaseProtocol(),
    )
    event = models.RenderedEvent(
        content=content,
        event_id=uuid.uuid4(),
        provider=provider,
        user_context={},
    )
    transient = transports.DeliveryError(
        "firebase_transport_error",
        [{"device_uuid": str(content.device_uuid), "code": "firebase_transport_error"}],
    )

    with (
        mock.patch.object(models.Provider, "send", side_effect=transient),
        mock.patch.object(models.RenderedEvent, "save"),
    ):
        before = datetime.datetime.now(datetime.timezone.utc)
        event.send()
        first_retry = event.next_retry_at
        event.send()
        second_retry = event.next_retry_at

    assert 4 <= (first_retry - before).total_seconds() <= 6
    assert 9 <= (second_retry - before).total_seconds() <= 11
    assert event.status_description == "firebase_transport_error"
    assert event.errors[0]["permanent"] is False

    permanent = transports.DeliveryError(
        "registration_token_inactive",
        [
            {
                "device_uuid": str(content.device_uuid),
                "code": "registration_token_inactive",
            }
        ],
        permanent=True,
    )
    with (
        mock.patch.object(models.Provider, "send", side_effect=permanent),
        mock.patch.object(models.RenderedEvent, "save"),
        mock.patch.object(models.RenderedEvent, "update"),
    ):
        event.send()

    assert event.next_retry_at == event.last_retry_at + datetime.timedelta(days=365)
    assert event.status_description == "registration_token_inactive"
    assert event.errors == [
        {
            "device_uuid": str(content.device_uuid),
            "code": "registration_token_inactive",
            "permanent": True,
        }
    ]


def test_provider_model_cannot_select_a_credentials_file():
    provider = models.Provider(
        project_id=uuid.uuid4(),
        name="firebase",
        protocol=models.FirebaseProtocol(),
    )
    property_type = provider.properties["protocol"].property_type
    value = property_type.to_simple_type(provider.protocol)
    assert value == {"kind": "firebase", "ttl_seconds": 3600}


def test_legacy_firebase_provider_config_loads_without_selecting_credentials():
    provider = models.Provider(
        project_id=uuid.uuid4(),
        name="firebase",
        protocol=models.FirebaseProtocol(),
    )
    property_type = provider.properties["protocol"].property_type

    protocol = property_type.from_simple_type(
        {
            "kind": "firebase",
            "firebase_project_id": "legacy-project",
            "credentials_file": "/legacy/private-credential.json",
            "ttl_seconds": 900,
        }
    )

    assert isinstance(protocol, models.FirebaseProtocol)
    assert property_type.to_simple_type(protocol) == {
        "kind": "firebase",
        "ttl_seconds": 900,
    }


def test_legacy_mobile_push_rows_load_and_use_the_platform_credential():
    project_id = uuid.uuid4()
    provider = models.Provider(
        project_id=project_id,
        name="firebase",
        protocol=models.FirebaseProtocol(ttl_seconds=900),
    )
    event_type = models.EventType(project_id=project_id, name="legacy-push")
    template = models.Template(
        project_id=project_id,
        name="legacy-push",
        provider=provider,
        event_type=event_type,
        params={},
        content=models.LegacyMobilePushContent(),
    )
    template_type = template.properties["content"].property_type
    assert isinstance(
        template_type.from_simple_type({"kind": "mobile_push"}),
        models.LegacyMobilePushContent,
    )

    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=600
    )
    content = models.LegacyRenderedMobilePushContent(
        device_uuid=uuid.UUID("00000000-0000-0000-0000-000000000009"),
        registration_token="legacy-token",
        payload={"kind": "encrypted", "ciphertext": "opaque"},
        expires_at=expires_at,
        priority="high",
    )
    rendered = models.RenderedEvent(
        event_id=uuid.uuid4(),
        provider=provider,
        user_context={},
        content=content,
    )
    rendered_type = rendered.properties["content"].property_type
    restored = rendered_type.from_simple_type(rendered_type.to_simple_type(content))
    assert isinstance(restored, models.LegacyRenderedMobilePushContent)

    session = mock.Mock()
    session.post.return_value.status_code = 200
    with (
        mock.patch.object(
            transports,
            "firebase_session",
            return_value=(session, "platform-project"),
        ),
        mock.patch(
            "restalchemy.storage.sql.orm.ObjectCollection.get_one_or_none",
            return_value=None,
        ),
    ):
        provider.send(restored, {})

    message = session.post.call_args.kwargs["json"]["message"]
    assert message["token"] == "legacy-token"
    assert message["data"] == {"payload": '{"kind":"encrypted","ciphertext":"opaque"}'}
    assert message["android"]["priority"] == "high"
    assert 1 <= int(message["android"]["ttl"].removesuffix("s")) <= 600


def test_firebase_session_uses_only_the_fixed_platform_credential():
    credentials = mock.Mock(project_id="firebase-project")
    session = mock.Mock()
    transports.firebase_session.cache_clear()
    try:
        with (
            mock.patch.object(
                service_account.Credentials,
                "from_service_account_file",
                return_value=credentials,
            ) as load,
            mock.patch.object(
                google_requests,
                "AuthorizedSession",
                return_value=session,
            ),
        ):
            assert transports.firebase_session() == (session, "firebase-project")

        load.assert_called_once_with(
            transports.FIREBASE_CREDENTIALS_FILE,
            scopes=[transports.FIREBASE_SCOPE],
        )
    finally:
        transports.firebase_session.cache_clear()
