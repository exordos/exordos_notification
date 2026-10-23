#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License.

import datetime
import functools
import json
import urllib.parse

import bazooka
from bazooka import exceptions as http_exceptions
from gcl_sdk.clients.http import base as http_client


class DeliveryPending(Exception):
    """Other deliveries can remain pending after an individual token failed."""

    def __init__(self, errors=()):
        super().__init__("upstream_pending")
        self.errors = list(errors)


class DeliveryError(Exception):
    def __init__(self, code, errors=(), permanent=False):
        super().__init__(code)
        self.code = code
        self.errors = list(errors)
        self.permanent = permanent


class RelayHttpSession(bazooka.Client.SESSION):
    def _sanitize_body(self, body):
        return "[REDACTED]"

    @staticmethod
    def _response_body_for_log(response):
        return "[REDACTED]"


class RelayHttpClient(bazooka.Client):
    SESSION = RelayHttpSession

    def request(self, method, url, **kwargs):
        # Bazooka's allow_redirects constructor option only reaches GET, not POST.
        kwargs["allow_redirects"] = False
        response = super().request(method, url, **kwargs)
        if 300 <= response.status_code < 400:
            raise DeliveryError("relay_redirect", permanent=True)
        return response


@functools.lru_cache(maxsize=16)
def relay_client(endpoint, auth_file):
    if urllib.parse.urlsplit(endpoint).scheme != "https":
        raise DeliveryError("insecure_relay_endpoint", permanent=True)
    # Credentials are delivered as a private server configuration file.
    client = RelayHttpClient(default_timeout=10)
    with open(auth_file) as stream:
        auth = http_client.CoreIamAuthenticator(**json.load(stream), http_client=client)
    return http_client.CollectionBaseClient(
        base_url=endpoint.rstrip("/"),
        http_client=client,
        auth=auth,
    )


def send_relay(protocol, content):
    client = relay_client(protocol.endpoint, protocol.auth_file)
    payload = content.payload
    try:
        result = client.get("/v1/events/", payload["uuid"])
    except http_exceptions.NotFoundError:
        try:
            result = client.create("/v1/events/", data=payload)
        except http_exceptions.ConflictError:
            result = client.get("/v1/events/", payload["uuid"])
    errors = result.get("errors", [])
    if result["status"] in ("NEW", "IN_PROGRESS"):
        raise DeliveryPending(errors)
    if errors:
        permanent = all(
            error.get("permanent", error.get("code") == "registration_token_inactive")
            for error in errors
        )
        raise DeliveryError("upstream_delivery_failed", errors, permanent)
    if result["status"] == "ACTIVE":
        return
    if result["status"] == "ERROR":
        raise DeliveryError("upstream_delivery_failed")
    raise DeliveryPending()


@functools.lru_cache(maxsize=16)
def firebase_session(credentials_file):
    from google.auth.transport import requests as google_requests
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(
        credentials_file,
        scopes=["https://www.googleapis.com/auth/firebase.messaging"],
    )
    return google_requests.AuthorizedSession(credentials)


def send_firebase(protocol, content):
    now = datetime.datetime.now(datetime.timezone.utc)
    ttl = min(protocol.ttl_seconds, int((content.expires_at - now).total_seconds()))
    if ttl <= 0:
        raise DeliveryError("expired", permanent=True)
    data = {
        "payload": json.dumps(
            content.payload, ensure_ascii=False, separators=(",", ":")
        ),
    }
    if len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()) > 4096:
        raise DeliveryError("payload_too_large", permanent=True)
    request = {
        "message": {
            "token": content.registration_token,
            "data": data,
            "android": {
                "priority": (
                    "high"
                    if content.payload["kind"] == "workspace_message_create"
                    else "normal"
                ),
                "ttl": f"{ttl}s",
            },
        },
    }
    session = firebase_session(protocol.credentials_file)
    response = session.post(
        f"https://fcm.googleapis.com/v1/projects/{protocol.firebase_project_id}/messages:send",
        json=request,
        timeout=10,
    )
    if response.status_code == 200:
        return
    details = response.json().get("error", {}).get("details", [])
    unregistered = any(
        detail.get("@type") == "type.googleapis.com/google.firebase.fcm.v1.FcmError"
        and detail.get("errorCode") == "UNREGISTERED"
        for detail in details
    )
    if unregistered:
        raise DeliveryError(
            "registration_token_inactive",
            [
                {
                    "device_uuid": str(content.device_uuid),
                    "code": "registration_token_inactive",
                }
            ],
            permanent=True,
        )
    # Do not persist or log provider response bodies, which may echo input data.
    raise DeliveryError(
        f"fcm_http_{response.status_code}",
        permanent=400 <= response.status_code < 500 and response.status_code != 429,
    )
