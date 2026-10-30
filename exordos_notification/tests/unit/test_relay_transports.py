# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").
import logging
import unittest
from unittest import mock

from exordos_notification.common import transports
from exordos_notification.dm import models
from exordos_notification.tests.unit import test_push_transports

push_event = test_push_transports.push_event


class RelayTransportTest(unittest.TestCase):
    def test_cleartext_relay_is_rejected_before_reading_credentials(self):
        with (
            mock.patch("builtins.open") as credentials,
            self.assertRaises(transports.DeliveryError) as raised,
        ):
            transports.relay_client(
                "http://notification.example.com", "/test/auth.json"
            )
        credentials.assert_not_called()
        self.assertEqual("insecure_relay_endpoint", raised.exception.code)
        self.assertTrue(raised.exception.permanent)

    def test_persisted_upstream_event_is_not_created_again(self):
        event, _ = push_event()
        content = models.RenderedNotificationContent(payload=event.notification_event())
        protocol = models.NotificationProtocol(
            endpoint="https://notification.example.com", auth_file="/test/auth.json"
        )
        with mock.patch.object(transports, "relay_client") as client:
            client.return_value.get.return_value = {"status": "ACTIVE"}
            protocol.send(content, {})
        client.return_value.create.assert_not_called()

    def test_queued_event_is_not_reported_as_fcm_accepted(self):
        event, _ = push_event()
        content = models.RenderedNotificationContent(payload=event.notification_event())
        protocol = models.NotificationProtocol(
            endpoint="https://notification.example.com", auth_file="/test/auth.json"
        )
        with mock.patch.object(transports, "relay_client") as client:
            client.return_value.get.return_value = {"status": "NEW"}
            with self.assertRaises(transports.DeliveryPending):
                protocol.send(content, {})


def test_relay_response_body_is_redacted_even_if_upstream_echoes_input(caplog):
    response = mock.Mock(
        url="https://notification.example.com/v1/events/",
        status_code=400,
        reason="Bad Request",
    )
    response.text = "synthetic-FCM-token-in-error-body"
    with (
        caplog.at_level(logging.DEBUG),
        transports.RelayHttpSession(None, True) as session,
    ):
        session._log_response(response)
    assert "status code 400" in caplog.text
    assert response.text not in caplog.text


def test_relay_passes_complete_event_unchanged():
    event, template = push_event()
    template.content = models.NotificationContent()
    rendered = test_push_transports.PushRenderingTest().render(event, template)
    assert len(rendered) == 1
    assert rendered[0].content.payload["uuid"] == str(event.uuid)
    assert rendered[0].content.payload["event_params"] == event.event_params
