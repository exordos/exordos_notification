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

import datetime
from email import utils as email_utils
import functools
import json
import math
import pathlib
import tempfile
from urllib import parse as urlparse
import uuid

import bazooka
from gcl_sdk.clients.http import base as http_client
from google.auth.transport import requests as google_requests
from google.oauth2 import service_account


FIREBASE_CREDENTIALS_FILE = "/etc/exordos_notification/firebase-service-account.json"
RELAY_CREDENTIALS_FILE = "/etc/exordos_notification/workspace-mesh-upstream.json"
APNS_RELAY_CREDENTIALS_FILE = "/etc/exordos_notification/apns-relay-upstream.json"
FIREBASE_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


class DeliveryError(RuntimeError):
    """A sanitized transport result with retry and target metadata."""

    def __init__(self, code, errors=(), permanent=False, retry_after=None):
        super().__init__(code)
        self.code = code
        self.errors = list(errors)
        self.permanent = permanent
        self.retry_after = retry_after


class DeliveryPending(RuntimeError):
    """An upstream Notification has accepted the event and is still processing it."""


class RelayHttpSession(bazooka.Client.SESSION):
    def _sanitize_body(self, body):
        return "[REDACTED]"

    @staticmethod
    def _response_body_for_log(response):
        return "[REDACTED]"


class RelayHttpClient(bazooka.Client):
    SESSION = RelayHttpSession

    def __init__(self, *, trust_pem=None, **kwargs):
        super().__init__(**kwargs)
        self._trust_file = None
        if trust_pem:
            # Requests expects a file. Keep it for the cached client's lifetime.
            self._trust_dir = tempfile.TemporaryDirectory(prefix="notification-relay-")
            trust_file = pathlib.Path(self._trust_dir.name) / "ca.pem"
            trust_file.write_text(trust_pem, encoding="utf-8")
            self._trust_file = str(trust_file)

    def request(self, method, url, **kwargs):
        kwargs["allow_redirects"] = False
        if self._trust_file is not None:
            kwargs["verify"] = self._trust_file
        response = super().request(method, url, **kwargs)
        if 300 <= response.status_code < 400:
            raise DeliveryError("relay_redirect", permanent=True)
        return response


def _https_endpoint(url):
    target = urlparse.urlsplit(url)
    if (
        target.scheme != "https"
        or not target.hostname
        or target.username is not None
        or target.query
        or target.fragment
    ):
        raise ValueError("Invalid relay HTTPS endpoint")
    return url.rstrip("/")


@functools.lru_cache(maxsize=2)
def relay_destination(transport):
    """Read the Core-managed connection once per transport and worker."""
    path = APNS_RELAY_CREDENTIALS_FILE if transport == "apns" else RELAY_CREDENTIALS_FILE
    try:
        with open(path, encoding="utf-8") as stream:
            config = json.load(stream)
        endpoint = _https_endpoint(config["notification_endpoint"])
        iam_endpoint = _https_endpoint(config["iam_endpoint"])
        event_type = str(uuid.UUID(config["event_type_uuid"]))
        project = str(uuid.UUID(config["project_uuid"]))
        access_token = config.get("access_token")
        refresh_token = config.get("refresh_token")
        client = RelayHttpClient(
            trust_pem=config.get("tls_trust_pem"), default_timeout=10
        )
        auth = http_client.CoreIamAuthenticator(
            base_url=iam_endpoint,
            access_token=access_token,
            refresh_token=refresh_token,
            http_client=client,
        )
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        raise DeliveryError("relay_configuration_error", permanent=True) from None
    return (
        http_client.CollectionBaseClient(
            base_url=endpoint, http_client=client, auth=auth
        ),
        event_type,
        project,
        f"notification:{endpoint}:{event_type}",
    )


def retry_after_seconds(response, now=None):
    """Return a non-negative Retry-After delay for seconds or an HTTP date."""

    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        try:
            deadline = email_utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=datetime.timezone.utc)
    current = now or datetime.datetime.now(datetime.timezone.utc)
    return max(0, math.ceil((deadline - current).total_seconds()))


@functools.lru_cache(maxsize=1)
def firebase_session():
    """Build the FCM session from the single platform-managed credential."""

    credentials = service_account.Credentials.from_service_account_file(
        FIREBASE_CREDENTIALS_FILE,
        scopes=[FIREBASE_SCOPE],
    )
    if not credentials.project_id:
        raise RuntimeError("firebase_credentials_missing_project_id")
    return google_requests.AuthorizedSession(credentials), credentials.project_id
