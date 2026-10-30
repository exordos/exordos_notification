# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").

import json
import uuid
from unittest import mock

import pytest
from google.auth.transport import requests as google_requests
from google.oauth2 import service_account
from restalchemy.common import exceptions

from exordos_notification.common import transports
from exordos_notification.dm import models


def firebase_content(**overrides):
    values = {
        "registration_token": "token-{{ suffix }}",
        "data": {"payload": "{{ encrypted_payload }}"},
        "android_priority": "{{ priority }}",
    }
    values.update(overrides)
    return models.FirebaseContent(**values)


def rendered_content():
    return firebase_content().render(
        {
            "suffix": "one",
            "encrypted_payload": '{"kind":"encrypted","ciphertext":"opaque"}',
            "priority": "high",
        }
    )


def test_firebase_template_renders_only_string_data():
    content = rendered_content()
    assert content.registration_token == "token-one"
    assert content.data == {"payload": '{"kind":"encrypted","ciphertext":"opaque"}'}
    assert content.android_priority == "high"

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
            "suffix": "one",
            "encrypted_payload": '{"kind":"encrypted"}',
            "priority": "normal",
        },
    )
    iam = mock.Mock()

    with mock.patch.object(models.Event, "get_templates", return_value=[template]):
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
        pytest.raises(
            RuntimeError,
            match="^firebase_registration_token_inactive$",
        ),
    ):
        protocol.send(content, {})


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
        pytest.raises(RuntimeError, match="^firebase_http_503$"),
    ):
        protocol.send(content, {})


def test_provider_model_cannot_select_a_credentials_file():
    provider = models.Provider(
        project_id=uuid.uuid4(),
        name="firebase",
        protocol=models.FirebaseProtocol(),
    )
    property_type = provider.properties["protocol"].property_type
    value = property_type.to_simple_type(provider.protocol)
    assert value == {"kind": "firebase", "ttl_seconds": 3600}


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
