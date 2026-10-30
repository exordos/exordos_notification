#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License.

import functools


class DeliveryError(Exception):
    def __init__(self, code, errors=(), permanent=False):
        super().__init__(code)
        self.code = code
        self.errors = list(errors)
        self.permanent = permanent


@functools.lru_cache(maxsize=16)
def firebase_session(credentials_file):
    from google.auth.transport import requests as google_requests
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(
        credentials_file,
        scopes=["https://www.googleapis.com/auth/firebase.messaging"],
    )
    return google_requests.AuthorizedSession(credentials)
