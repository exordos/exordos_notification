#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License.

import base64
import datetime
import json
import unittest
import uuid
from unittest import mock

from oslo_config import cfg

from exordos_notification.common import constants, transports
from exordos_notification.dm import models


def push_event(kind=constants.NOTIFICATION_MESSAGE_CREATE):
    project = uuid.uuid4()
    event_type = models.EventType(
        project_id=project,
        uuid=constants.PUSH_EVENT_TYPES[kind],
    )
    event = models.Event(
        project_id=project,
        event_type=event_type,
        exchange=models.UserExchange(user_uuid=uuid.uuid4()),
        event_params={
            "kind": kind,
            **{
                field: str(uuid.uuid4())
                for field in (
                    "organization_uuid",
                    "stream_uuid",
                    "topic_uuid",
                    "message_uuid",
                )
            },
            "project_uuid": str(project),
            "devices": [
                {
                    "device_uuid": str(uuid.uuid4()),
                    "registration_token": f"test-registration-{number}",
                    "content": {
                        "kind": "encrypted",
                        "key_uuid": str(uuid.uuid4()),
                        "algorithm": "HPKE-v1-BASE-X25519-HKDF-SHA256-AES-256-GCM",
                        "enc": base64.urlsafe_b64encode(bytes(range(32)))
                        .rstrip(b"=")
                        .decode(),
                        "ciphertext": base64.urlsafe_b64encode(bytes(range(16)))
                        .rstrip(b"=")
                        .decode(),
                    },
                }
                for number in range(2)
            ],
        },
    )
    provider = models.Provider(
        project_id=project,
        name="Firebase",
        protocol=models.FirebaseProtocol(
            firebase_project_id="test-project",
            credentials_file="/test/credentials.json",
        ),
    )
    template = models.Template(
        project_id=project,
        name="Push",
        event_type=event_type,
        provider=provider,
        params={},
        content=models.MobilePushContent(),
    )
    return event, template


class PushRenderingTest(unittest.TestCase):
    def render(self, event, template):
        iam = mock.Mock()
        with (
            mock.patch.object(models.Template, "objects") as templates,
            mock.patch.object(models.Binding, "objects") as bindings,
        ):
            templates.get_all.return_value = [template]
            bindings.get_all.return_value = []
            rendered = event.render(iam)
        iam.get_user.assert_not_called()
        return rendered

    def test_firebase_expands_devices_without_foreign_iam_lookup(self):
        event, template = push_event()
        rendered = self.render(event, template)
        self.assertEqual(2, len(rendered))
        for number, item in enumerate(rendered):
            self.assertEqual(event.uuid, item.event_id)
            self.assertEqual(
                event.event_params["devices"][number]["content"],
                item.content.payload["content"],
            )
            self.assertEqual(
                event.event_params["message_uuid"], item.content.payload["message_uuid"]
            )
            self.assertNotIn("registration_token", item.content.payload)
            self.assertNotIn("devices", item.content.payload)

    def test_foreign_source_does_not_use_orion_user_bindings(self):
        event, template = push_event()
        cfg.CONF.set_override("local_organization_uuid", uuid.uuid4(), group="push")
        try:
            with (
                mock.patch.object(models.Template, "objects") as templates,
                mock.patch.object(models.Binding, "objects") as bindings,
            ):
                templates.get_all.return_value = [template]
                rendered = event.render(mock.Mock())
                bindings.get_all.assert_not_called()
            self.assertEqual(2, len(rendered))
        finally:
            cfg.CONF.clear_override("local_organization_uuid", group="push")


class FirebaseTransportTest(unittest.TestCase):
    def setUp(self):
        event, template = push_event()
        self.protocol = template.provider.protocol
        self.content = template.content.render(
            {
                "_notification_event": event.notification_event(),
                "_notification_expires_at": event.notification_expires_at(),
            }
        )[0]

    def test_sends_encrypted_data_only(self):
        with mock.patch.object(transports, "firebase_session") as session:
            session.return_value.post.return_value.status_code = 200
            self.protocol.send(self.content, {})
        message = session.return_value.post.call_args.kwargs["json"]["message"]
        self.assertNotIn("notification", message)
        self.assertEqual(self.content.payload, json.loads(message["data"]["payload"]))
        self.assertEqual("high", message["android"]["priority"])

    def test_cancellation_uses_normal_priority(self):
        self.content.payload["kind"] = "notification_message_cancel"
        with mock.patch.object(transports, "firebase_session") as session:
            session.return_value.post.return_value.status_code = 200
            self.protocol.send(self.content, {})
        self.assertEqual(
            "normal",
            session.return_value.post.call_args.kwargs["json"]["message"]["android"][
                "priority"
            ],
        )

    def test_unregistered_is_reported_for_exact_device(self):
        with mock.patch.object(transports, "firebase_session") as session:
            response = session.return_value.post.return_value
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
            with self.assertRaises(transports.DeliveryError) as raised:
                self.protocol.send(self.content, {})
        self.assertTrue(raised.exception.permanent)
        self.assertEqual(
            [
                {
                    "device_uuid": str(self.content.device_uuid),
                    "code": "registration_token_inactive",
                }
            ],
            raised.exception.errors,
        )

    def test_invalid_argument_does_not_classify_token_as_inactive(self):
        with mock.patch.object(transports, "firebase_session") as session:
            response = session.return_value.post.return_value
            response.status_code = 400
            response.json.return_value = {
                "error": {
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError",
                            "errorCode": "INVALID_ARGUMENT",
                        }
                    ]
                }
            }
            with self.assertRaises(transports.DeliveryError) as raised:
                self.protocol.send(self.content, {})
        self.assertNotEqual("registration_token_inactive", raised.exception.code)
        self.assertEqual([], raised.exception.errors)

    def test_unicode_size_is_checked_before_network_access(self):
        self.content.payload["content"]["ciphertext"] = "🦉" * 1100
        with (
            mock.patch.object(transports, "firebase_session") as session,
            self.assertRaises(transports.DeliveryError) as raised,
        ):
            self.protocol.send(self.content, {})
        self.assertEqual("payload_too_large", raised.exception.code)
        session.assert_not_called()

    def test_expired_message_is_not_sent(self):
        self.content.expires_at = datetime.datetime.now(
            datetime.timezone.utc
        ) - datetime.timedelta(seconds=1)
        with (
            mock.patch.object(transports, "firebase_session") as session,
            self.assertRaises(transports.DeliveryError) as raised,
        ):
            self.protocol.send(self.content, {})
        self.assertEqual("expired", raised.exception.code)
        session.assert_not_called()


def test_python_producer_fixture_is_forwarded_without_content_enrichment():
    import pathlib

    fixture = json.loads(
        (
            pathlib.Path(__file__).parents[1] / "fixtures/mobile_push_event.json"
        ).read_text()
    )
    rendered = models.MobilePushContent().render(
        {
            "_notification_event": fixture["event"],
            "_notification_expires_at": datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=1),
        }
    )
    assert len(rendered) == 1
    assert rendered[0].payload == fixture["payload"]
    assert "registration_token" not in json.dumps(rendered[0].payload)
