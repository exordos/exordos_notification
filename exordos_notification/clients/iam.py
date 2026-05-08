#    Copyright 2025 Genesis Corporation.
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

from bazooka import common
from bazooka import client


class IAMClient(common.RESTClientMixIn):
    USER_PATH = "iam/users"

    def __init__(self, endpoint, token, timeout=5):
        super().__init__()
        self._client = client.Client(default_timeout=timeout)
        self._endpoint = endpoint
        self._token = token

    def _build_headers(self, **kwargs):
        headers = kwargs.copy()
        headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def get_user(self, user_id):
        url = self._build_resource_uri([self.USER_PATH, user_id])
        return self._client.get(
            url,
            headers=self._build_headers(),
        ).json()
