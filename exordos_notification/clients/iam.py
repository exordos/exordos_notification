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

import uuid as sys_uuid

import bazooka
from gcl_sdk.clients.http import base


class IAMClient(base.CollectionBaseClient):
    """IAM client using CoreIamAuthenticator for auto-refresh tokens."""

    USER_PATH = "v1/iam/users/"

    def __init__(
        self,
        endpoint,
        username,
        password,
        client_id="GenesisCoreClientId",
        client_secret="GenesisCoreSecret",
        client_uuid=sys_uuid.UUID("00000000-0000-0000-0000-000000000000"),
        timeout=5,
    ):
        auth = base.CoreIamAuthenticator(
            base_url=endpoint,
            username=username,
            password=password,
            client_id=client_id,
            client_secret=client_secret,
            client_uuid=client_uuid,
        )
        super().__init__(
            base_url=endpoint,
            http_client=bazooka.Client(default_timeout=timeout),
            auth=auth,
        )

    def get_user(self, user_id):
        return self.get(self.USER_PATH, sys_uuid.UUID(str(user_id)))
