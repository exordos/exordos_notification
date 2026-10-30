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

import functools

FIREBASE_CREDENTIALS_FILE = "/etc/exordos_notification/firebase-service-account.json"
FIREBASE_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


@functools.lru_cache(maxsize=1)
def firebase_session():
    """Build the FCM session from the single platform-managed credential."""

    from google.auth.transport import requests as google_requests
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(
        FIREBASE_CREDENTIALS_FILE,
        scopes=[FIREBASE_SCOPE],
    )
    if not credentials.project_id:
        raise RuntimeError("firebase_credentials_missing_project_id")
    return google_requests.AuthorizedSession(credentials), credentials.project_id
