#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License.

import functools
import json
import urllib.parse

import bazooka
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
        auth_config = json.load(stream)
    if urllib.parse.urlsplit(auth_config["base_url"]).scheme != "https":
        raise DeliveryError("insecure_relay_auth_endpoint", permanent=True)
    auth = http_client.CoreIamAuthenticator(**auth_config, http_client=client)
    return http_client.CollectionBaseClient(
        base_url=endpoint.rstrip("/"),
        http_client=client,
        auth=auth,
    )


@functools.lru_cache(maxsize=16)
def firebase_session(credentials_file):
    from google.auth.transport import requests as google_requests
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(
        credentials_file,
        scopes=["https://www.googleapis.com/auth/firebase.messaging"],
    )
    return google_requests.AuthorizedSession(credentials)
