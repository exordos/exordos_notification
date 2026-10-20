#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License.

import uuid

from oslo_config import cfg

from exordos_notification.common import constants

cfg.CONF.register_opt(
    cfg.StrOpt(
        "local-organization-uuid",
        help="Local Workspace identity; forwarded mobile events use only default transports.",
    ),
    group="push",
)


def use_local_bindings(params):
    local = cfg.CONF.push.local_organization_uuid
    return local is None or uuid.UUID(str(local)) == uuid.UUID(
        params["organization_uuid"]
    )


def is_push_event(event_type, params):
    reference = (
        str(getattr(event_type, "uuid", event_type)).rstrip("/").rsplit("/", 1)[-1]
    )
    kind = params.get("kind", "") if isinstance(params, dict) else ""
    return (
        reference in {str(value) for value in constants.PUSH_EVENT_TYPES.values()}
        or isinstance(kind, str)
        and kind.startswith("workspace_message_")
    )
