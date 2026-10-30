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

import enum
import uuid

GLOBAL_SERVICE_NAME = "exordos_notification"
NOTIFICATION_MESSAGE_CREATE = "notification_message_create"
NOTIFICATION_MESSAGE_CANCEL = "notification_message_cancel"
NOTIFICATION_TOPIC_UPDATE = "notification_topic_update"
PUSH_EVENT_TYPES = {
    kind: uuid.uuid5(uuid.NAMESPACE_URL, f"urn:exordos:notification:{kind}")
    for kind in (
        NOTIFICATION_MESSAGE_CREATE,
        NOTIFICATION_MESSAGE_CANCEL,
        NOTIFICATION_TOPIC_UPDATE,
    )
}


class AlwaysActiveStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"


class EventStatus(str, enum.Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"
