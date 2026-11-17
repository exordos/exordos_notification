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
import functools
import math
from email import utils as email_utils

from google.auth.transport import requests as google_requests
from google.oauth2 import service_account

FIREBASE_CREDENTIALS_FILE = "/etc/exordos_notification/firebase-service-account.json"
FIREBASE_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


class DeliveryError(RuntimeError):
    """A sanitized transport result with retry and target metadata."""

    def __init__(self, code, errors=(), permanent=False, retry_after=None):
        super().__init__(code)
        self.code = code
        self.errors = list(errors)
        self.permanent = permanent
        self.retry_after = retry_after


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
