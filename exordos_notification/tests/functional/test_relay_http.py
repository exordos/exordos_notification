#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License.

import http.server
import json
import threading
from unittest import mock

import pytest

from exordos_notification.common import transports
from exordos_notification.dm import models
from exordos_notification.services.builders import agents
from exordos_notification.tests.functional.test_push_api import source_headers
from exordos_notification.tests.unit.test_push_transports import push_event


def test_relay_uses_real_v1_http_and_propagates_partial_inactive_error(
    http_server, tmp_path
):
    base, driver = http_server
    event, template = push_event()
    payload = event.notification_event()
    auth_file = tmp_path / "relay-auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "base_url": "http://unused.invalid/api/core",
                "access_token": source_headers(driver, payload)[
                    "Authorization"
                ].split()[1],
            }
        )
    )
    auth_file.chmod(0o600)
    relay = models.Provider(
        name="Customer relay",
        project_id=event.project_id,
        protocol=models.NotificationProtocol(endpoint=base, auth_file=str(auth_file)),
    )
    content = models.RenderedNotificationContent(payload=payload)
    # Real SDK, HTTP listener, JWT policies and PostgreSQL; no relay-client mock.
    for _ in range(2):
        with pytest.raises(transports.DeliveryPending):
            relay.send(content, {})
    saved = models.Event.objects.get_one(filters={"uuid": event.uuid})
    assert saved.event_params == event.event_params
    assert saved.exchange.user_uuid == event.exchange.user_uuid
    assert len(models.Event.objects.get_all(filters={"uuid": event.uuid})) == 1

    template.provider.insert()
    with mock.patch.object(models.Event, "get_templates", return_value=[template]):
        rendered = saved.render(mock.Mock())
    for item in rendered:
        item.insert()
    rejected, accepted = rendered
    error = {
        "device_uuid": str(rejected.content.device_uuid),
        "code": "registration_token_inactive",
        "permanent": True,
    }
    with mock.patch.object(
        transports,
        "send_firebase",
        side_effect=transports.DeliveryError(
            "registration_token_inactive", [error], permanent=True
        ),
    ):
        rejected.send()
    builder = agents.EventBuilderAgent(iam_client=mock.Mock())
    builder._sync_event_statuses()
    with pytest.raises(transports.DeliveryPending) as pending:
        relay.send(content, {})
    assert pending.value.errors == [error]
    inactive = models.InactiveToken.identify(
        f"notification:{base}", rejected.content.registration_token
    )
    assert models.InactiveToken.objects.get_one(filters={"uuid": inactive.uuid})

    with mock.patch.object(transports, "send_firebase"):
        accepted.send()
    builder._sync_event_statuses()
    with pytest.raises(transports.DeliveryError) as failed:
        relay.send(content, {})
    assert failed.value.permanent
    assert failed.value.errors == [error]
    assert content.payload == payload
    transports.relay_client.cache_clear()


def test_relay_does_not_follow_an_https_post_redirect_with_device_addresses(
    http_server, tmp_path
):
    base, driver = http_server
    event, _ = push_event()
    payload = event.notification_event()
    received = []

    class Sink(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            body = b'{"status":"ACTIVE","errors":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    sink = http.server.HTTPServer(("127.0.0.1", 0), Sink)
    thread = threading.Thread(target=sink.serve_forever, daemon=True)
    thread.start()
    auth = tmp_path / "redirect-auth.json"
    auth.write_text(
        json.dumps(
            {
                "base_url": "https://unused.example.com/api/core",
                "access_token": source_headers(driver, payload)[
                    "Authorization"
                ].split()[1],
            }
        )
    )
    auth.chmod(0o600)
    driver.redirect_post_target = f"http://127.0.0.1:{sink.server_port}/leak"
    failure = None
    try:
        protocol = models.NotificationProtocol(endpoint=base, auth_file=str(auth))
        try:
            transports.send_relay(
                protocol, models.RenderedNotificationContent(payload=payload)
            )
        except transports.DeliveryError as error:
            failure = error
        assert not received, (
            "The encrypted event and device addresses followed a plaintext redirect"
        )
        assert isinstance(failure, transports.DeliveryError)
        assert failure.code == "relay_redirect"
        assert failure.permanent
    finally:
        driver.redirect_post_target = None
        transports.relay_client.cache_clear()
        sink.shutdown()
        thread.join(timeout=5)
        sink.server_close()


@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
def test_relay_logs_never_include_device_tokens_or_encrypted_payloads(
    http_server, tmp_path, caplog, level
):
    base, driver = http_server
    event, _ = push_event()
    payload = event.notification_event()
    token = source_headers(driver, payload)["Authorization"].split()[1]
    auth = tmp_path / "logging-auth.json"
    auth.write_text(
        json.dumps({"base_url": "https://unused.example.com", "access_token": token})
    )
    auth.chmod(0o600)
    try:
        with caplog.at_level(level), pytest.raises(transports.DeliveryPending):
            transports.send_relay(
                models.NotificationProtocol(endpoint=base, auth_file=str(auth)),
                models.RenderedNotificationContent(payload=payload),
            )
        # Both ends run in this process. Inspect HTTP diagnostics separately
        # from the database driver's independent SQL parameter diagnostics.
        logged = "\n".join(
            record.getMessage()
            for record in caplog.records
            if record.name in ("bazooka.client", "restalchemy.api.middlewares.logging")
        )
        assert "HTTP(s) request:" in logged
        assert "[REDACTED]" in logged
        for target in payload["event_params"]["devices"]:
            assert target["registration_token"] not in logged
            assert target["content"]["ciphertext"] not in logged
        assert token not in logged
    finally:
        transports.relay_client.cache_clear()


@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
def test_relay_iam_login_and_refresh_never_log_credentials(tmp_path, caplog, level):
    base = "https://notification.example.com"

    class TokenHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps(
                {
                    "access_token": "synthetic-issued-access",
                    "refresh_token": "synthetic-issued-refresh",
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), TokenHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    auth = tmp_path / "iam-auth.json"
    auth.write_text(
        json.dumps(
            {
                "base_url": f"http://127.0.0.1:{server.server_port}",
                "username": "synthetic-user",
                "password": "synthetic-password",
            }
        )
    )
    auth.chmod(0o600)
    try:
        client = transports.relay_client(base, str(auth))
        with caplog.at_level(level):
            client._auth.authenticate()
            client._auth.authenticate()
        assert client._auth.get_auth_header() == {
            "Authorization": "Bearer synthetic-issued-access"
        }
        for secret in [
            "synthetic-password",
            "synthetic-issued-access",
            "synthetic-issued-refresh",
        ]:
            assert secret not in caplog.text
    finally:
        transports.relay_client.cache_clear()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
