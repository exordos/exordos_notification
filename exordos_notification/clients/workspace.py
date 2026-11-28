# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").

import json
import pathlib
from urllib import parse
import uuid

import requests

from exordos_notification.common import transports


CREDENTIALS_DIR = pathlib.Path("/etc/exordos_notification/workspace")
MESSENGER_API = "/api/workspace/v1/messenger"


class WorkspaceClient:
    def __init__(self, project_id: uuid.UUID | None, provider_uuid: uuid.UUID):
        # Keep the destination and its secret together in operator-managed files.
        # Provider and template API fields cannot change either of them.
        path = CREDENTIALS_DIR / str(project_id or "global") / f"{provider_uuid}.json"
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
            endpoint = parse.urlsplit(config["endpoint"])
            token = config["access_token"]
            if (
                endpoint.scheme != "https"
                or not endpoint.hostname
                or endpoint.username is not None
                or endpoint.path not in {"", "/"}
                or endpoint.query
                or endpoint.fragment
                or not isinstance(token, str)
                or not token
                or any(character.isspace() for character in token)
            ):
                raise ValueError("Invalid Workspace connection")
        except (AttributeError, KeyError, OSError, TypeError, ValueError):
            raise transports.DeliveryError("workspace_configuration_error") from None
        self._endpoint = endpoint.geturl().rstrip("/") + MESSENGER_API
        self._headers = {"Authorization": f"Bearer {token}"}

    def _post(self, collection: str, payload: dict) -> dict:
        try:
            response = requests.post(
                f"{self._endpoint}/{collection}/",
                headers=self._headers,
                json=payload,
                timeout=10,
                allow_redirects=False,
            )
        except requests.RequestException:
            raise transports.DeliveryError("workspace_transport_error") from None

        status = response.status_code
        if status not in {200, 201}:
            retry = status in {401, 408, 429} or 500 <= status < 600
            raise transports.DeliveryError(
                f"workspace_http_{status}",
                permanent=not retry,
                retry_after=transports.retry_after_seconds(response) if retry else None,
            )
        try:
            result = response.json()
            uuid.UUID(result["uuid"])
        except (AttributeError, KeyError, TypeError, ValueError):
            raise transports.DeliveryError("workspace_invalid_response") from None
        return result

    def direct_topic(self, user_uuid: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
        stream = self._post(
            "streams",
            {"name": "Direct", "direct_user_uuid": str(user_uuid)},
        )
        if stream.get("private") is not True or stream.get("direct_user_uuid") != str(
            user_uuid
        ):
            raise transports.DeliveryError("workspace_invalid_response")
        try:
            return uuid.UUID(stream["uuid"]), uuid.UUID(stream["default_topic_uuid"])
        except (AttributeError, KeyError, TypeError, ValueError):
            raise transports.DeliveryError(
                "workspace_direct_topic_unavailable", permanent=True
            ) from None

    def send_message(
        self,
        message_uuid: uuid.UUID,
        stream_uuid: uuid.UUID,
        topic_uuid: uuid.UUID,
        content: str,
    ) -> None:
        stream, topic = str(stream_uuid), str(topic_uuid)
        result = self._post(
            "messages",
            {
                "uuid": str(message_uuid),
                "stream_uuid": stream,
                "topic_uuid": topic,
                "payload": {"kind": "markdown", "content": content},
            },
        )
        if result.get("stream_uuid") != stream or result.get("topic_uuid") != topic:
            raise transports.DeliveryError("workspace_invalid_response")
