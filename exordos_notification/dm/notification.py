# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").

import base64
import binascii

from restalchemy.dm import models, properties, types, types_dynamic

from exordos_notification.common import constants


class CanonicalUUID(types.UUID):
    def from_simple_type(self, value):
        parsed = super().from_simple_type(value)
        if str(parsed) != value:
            raise ValueError("Expected a canonical UUID")
        return parsed


class Base64Url(types.String):
    def __init__(self, min_bytes, max_bytes=None):
        super().__init__(min_length=1)
        self.min_bytes = min_bytes
        self.max_bytes = max_bytes

    def validate(self, value):
        if not super().validate(value):
            return False
        try:
            decoded = base64.b64decode(
                value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
            )
        except (ValueError, binascii.Error):
            return False
        return (
            base64.urlsafe_b64encode(decoded).rstrip(b"=").decode() == value
            and len(decoded) >= self.min_bytes
            and (self.max_bytes is None or len(decoded) <= self.max_bytes)
        )


class EncryptedContent(types_dynamic.AbstractKindModel):
    KIND = "encrypted"
    algorithm = properties.property(
        types.Enum(["HPKE-v1-BASE-X25519-HKDF-SHA256-AES-256-GCM"]), required=True
    )
    key_uuid = properties.property(CanonicalUUID(), required=True)
    enc = properties.property(Base64Url(32, 32), required=True)
    ciphertext = properties.property(Base64Url(16), required=True)


class Device(models.Model):
    # KindModelType also parses plain property models without adding a wire kind.
    KIND = "device"
    device_uuid = properties.property(CanonicalUUID(), required=True)
    registration_token = properties.property(
        types.String(min_length=1, max_length=4096), required=True
    )
    content = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(EncryptedContent)
        ),
        required=True,
    )


class MessageCreate(types_dynamic.AbstractKindModel):
    KIND = constants.NOTIFICATION_MESSAGE_CREATE
    organization_uuid = properties.property(CanonicalUUID(), required=True)
    project_uuid = properties.property(CanonicalUUID(), required=True)
    stream_uuid = properties.property(CanonicalUUID(), required=True)
    topic_uuid = properties.property(CanonicalUUID(), required=True)
    message_uuid = properties.property(CanonicalUUID(), required=True)
    devices = properties.property(
        types.TypedList(types_dynamic.KindModelType(Device)), required=True
    )


class MessageCancel(MessageCreate):
    KIND = constants.NOTIFICATION_MESSAGE_CANCEL


class TopicUpdate(MessageCreate):
    KIND = constants.NOTIFICATION_TOPIC_UPDATE
    message_uuid = properties.property(types.AllowNone(CanonicalUUID()), default=None)
    priority = properties.property(types.Enum(["normal", "high"]), default="normal")


PARAMETERS = types_dynamic.KindModelSelectorType(
    types_dynamic.KindModelType(MessageCreate),
    types_dynamic.KindModelType(MessageCancel),
    types_dynamic.KindModelType(TopicUpdate),
)
