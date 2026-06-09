import logging

import bazooka

LOG = logging.getLogger(__name__)

_TOKEN_URL = "/v1/iam/clients/{client_uuid}/actions/get_token/invoke"
USER_COLLECTION = "/v1/iam/users/"
CLIENT_UUID = "default"
CLIENT_ID = "GenesisCoreClientId"
CLIENT_SECRET = "GenesisCoreSecret"


class IAMClient:
    """IAM client using direct bazooka HTTP calls."""

    USER_PATH = USER_COLLECTION

    def __init__(
        self,
        endpoint,
        username,
        password,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        client_uuid=CLIENT_UUID,
        timeout=5,
    ):
        self._endpoint = endpoint.rstrip("/")
        self._username = username
        self._password = password
        self._client_id = client_id
        self._client_secret = client_secret
        self._client_uuid = str(client_uuid)
        self._timeout = timeout
        self._http = bazooka.Client(default_timeout=timeout)
        self._token = None

    def _get_token(self):
        url = self._endpoint + _TOKEN_URL.format(client_uuid=self._client_uuid)
        resp = self._http.post(
            url,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Client-Id": self._client_id,
                "X-Client-Secret": self._client_secret,
            },
            data={
                "grant_type": "password",
                "username": self._username,
                "password": self._password,
                "scope": "",
                "ttl": "86400",
            },
        )
        return resp.json()["access_token"]

    def _auth_headers(self):
        if self._token is None:
            self._token = self._get_token()
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, method, path, **kwargs):
        url = self._endpoint + path
        try:
            resp = self._http.request(
                method, url, headers=self._auth_headers(), **kwargs
            )
        except Exception:
            self._token = None
            self._token = self._get_token()
            resp = self._http.request(
                method,
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                **kwargs,
            )
        return resp.json()

    def filter(self, path, **filters):
        params = {
            k: str(v).lower() if isinstance(v, bool) else str(v)
            for k, v in filters.items()
        }
        return self._request("GET", path, params=params)

    def get(self, path, uuid):
        return self._request("GET", f"{path}{uuid}")

    def get_user(self, user_id):
        return self.get(USER_COLLECTION, str(user_id))
