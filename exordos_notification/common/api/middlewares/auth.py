#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License.

import re

from gcl_iam import middlewares

from exordos_notification.common import push
from exordos_notification.dm import models


class NotificationAuthMiddleware(middlewares.GenesisCoreAuthMiddleware):
    """Keep legacy public events while protecting mobile events on the same v1 API."""

    def _should_skip_auth(self, request):
        if request.method == "POST" and request.path == "/v1/events/":
            try:
                body = request.json_body
            except ValueError:
                return True  # The regular parser will reject malformed JSON.
            if isinstance(body, dict):
                return not push.is_push_event(
                    body.get("event_type"), body.get("event_params")
                )
        if request.method == "GET" and re.fullmatch(
            r"/v1/events/[0-9a-fA-F-]{36}", request.path
        ):
            event = models.Event.objects.get_one_or_none(
                filters={"uuid": request.path.rsplit("/", 1)[-1]}
            )
            if event is not None:
                return not push.is_push_event(event.event_type, event.event_params)
        return super()._should_skip_auth(request)
