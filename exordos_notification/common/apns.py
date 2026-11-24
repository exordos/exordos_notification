# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").

import functools
import json
import re
import time

import httpcore
import jwt

from exordos_notification.common import transports

CREDENTIALS_FILE = "/etc/exordos_notification/apns-credentials.json"
ENDPOINTS = {
    "production": "https://api.push.apple.com",
    "sandbox": "https://api.sandbox.push.apple.com",
}
TIMEOUT = {"connect": 10, "read": 10, "write": 10, "pool": 10}
TOKEN_LIFETIME = 45 * 60
MAX_PAYLOAD_BYTES = 4096


class Connection:
    def __init__(self, config):
        self.team_id = config["team_id"]
        self.key_id = config["key_id"]
        self.private_key = config["private_key"]
        self.client = httpcore.ConnectionPool(http1=False, http2=True)
        self._issued_at = None
        self._token = None

    def authorization(self, now):
        if self._issued_at is None or now - self._issued_at >= TOKEN_LIFETIME:
            self._token = jwt.encode(
                {"iss": self.team_id, "iat": now},
                self.private_key,
                algorithm="ES256",
                headers={"kid": self.key_id},
            )
            self._issued_at = now
        return f"bearer {self._token}"

    def invalidate_authorization(self):
        self._issued_at = None


@functools.lru_cache(maxsize=len(ENDPOINTS))
def connection(environment):
    with open(CREDENTIALS_FILE, encoding="utf-8") as stream:
        config = json.load(stream)[environment]
    return Connection(config)


def _error(content, code, *, permanent=False, retry_after=None):
    return transports.DeliveryError(
        code,
        [{"device_uuid": str(content.device_uuid), "code": code}],
        permanent=permanent,
        retry_after=retry_after,
    )


def send(topic, content):
    token = content.registration_token
    if not re.fullmatch(r"(?:[0-9a-f]{2})+", token):
        raise _error(content, "apns_invalid_address", permanent=True)

    alert = content.priority == "high"
    body = json.dumps(
        content.payload,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    if len(body) > MAX_PAYLOAD_BYTES:
        raise _error(content, "apns_payload_too_large", permanent=True)

    now = int(time.time())
    expiration = int(content.expires_at.timestamp())
    if expiration <= now:
        raise _error(content, "apns_expired", permanent=True)
    try:
        session = connection(content.environment)
        authorization = session.authorization(now)
    except (OSError, KeyError, TypeError, ValueError, jwt.PyJWTError):
        raise _error(content, "apns_credentials_error") from None

    # A background status update must not replace a pending visible alert.
    collapse_id = content.collapse_id if alert else f"bg-{content.collapse_id[:61]}"
    try:
        response = session.client.request(
            "POST",
            f"{ENDPOINTS[content.environment]}/3/device/{token}",
            headers={
                "authorization": authorization,
                "content-type": "application/json",
                "apns-topic": topic,
                "apns-push-type": "alert" if alert else "background",
                "apns-priority": "10" if alert else "5",
                "apns-id": str(content.event_uuid),
                "apns-collapse-id": collapse_id,
                "apns-expiration": str(expiration),
            },
            content=body,
            extensions={"timeout": TIMEOUT},
        )
    except (
        httpcore.NetworkError,
        httpcore.TimeoutException,
        httpcore.ProtocolError,
    ):
        raise _error(content, "apns_transport_error") from None
    if response.status == 200:
        return
    try:
        reason = json.loads(response.content).get("reason")
    except (AttributeError, UnicodeError, ValueError):
        reason = None
    if response.status == 410 and reason in ("Unregistered", "ExpiredToken"):
        # APNs can accept this same token again after the app re-registers.
        # Leave invalidation to the producer's current registration snapshot.
        raise _error(content, "registration_token_inactive", permanent=True)
    if reason == "ExpiredProviderToken":
        session.invalidate_authorization()
    raise _error(
        content,
        f"apns_http_{response.status}",
        permanent=response.status not in (429, 500, 503)
        and reason != "ExpiredProviderToken",
    )
