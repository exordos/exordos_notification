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

import datetime
from email.mime import multipart
from email.mime import text
import hashlib
import json
import logging
import smtplib
import ssl
import uuid as sys_uuid

from bazooka import exceptions as http_exceptions
from gcl_sdk.agents.universal.dm import models as ua_models
from google.auth import exceptions as google_auth_exceptions
import jinja2
from psycopg.types import json as sql_json
import requests
from restalchemy.common import exceptions
from restalchemy.dm import filters
from restalchemy.dm import models
from restalchemy.dm import properties
from restalchemy.dm import relationships
from restalchemy.dm import types
from restalchemy.dm import types_dynamic
from restalchemy.storage.sql import orm
import zulip

from exordos_notification.common import apns
from exordos_notification.common import constants as c
from exordos_notification.common import transports


LOG = logging.getLogger(__name__)


def next_time(seconds):

    def calulator():
        now = datetime.datetime.now(datetime.timezone.utc)
        delta = datetime.timedelta(seconds=seconds)
        return now + delta

    return calulator


class ModelWithAlwaysActiveStatus(models.Model):
    STATUS = c.AlwaysActiveStatus

    status = properties.property(
        types.Enum([s.value for s in c.AlwaysActiveStatus]),
        default=STATUS.ACTIVE.value,
    )


class SimpleSmtpProtocol(types_dynamic.AbstractKindModel):
    KIND = "SimpleSMTP"

    host = properties.property(
        types.String(min_length=1, max_length=128),
        required=True,
    )
    port = properties.property(
        types.Integer(min_value=1, max_value=65535),
        required=True,
    )
    noreply_email_address = properties.property(
        types.Email(),
        required=True,
    )

    def _build_message(self, content, user_context):
        msg = multipart.MIMEMultipart("alternative")
        msg["From"] = self.noreply_email_address
        msg["To"] = content.recipient or user_context["user"]["email"]
        msg["Subject"] = content.title
        for body in content.bodies:
            msg.attach(text.MIMEText(body, "html", "utf-8"))
        return msg

    def _authenticate(self, smtp):
        return smtp

    def send(self, content, user_context):
        msg = self._build_message(content, user_context)
        with smtplib.SMTP(self.host, self.port) as smtp:
            smtp = self._authenticate(smtp)
            return smtp.sendmail(
                from_addr=self.noreply_email_address,
                to_addrs=content.recipient or user_context["user"]["email"],
                msg=msg.as_string(),
            )


class StartTlsSmtpProtocol(SimpleSmtpProtocol):
    KIND = "StartTlsSMTP"

    user = properties.property(
        types.String(max_length=128, min_length=1),
        required=True,
    )
    password = properties.property(
        types.String(max_length=256, min_length=1),
        required=True,
    )
    skip_tls_verify = properties.property(
        types.Boolean(),
        default=False,
    )

    def _authenticate(self, smtp):
        smtp.ehlo()
        ctx = ssl.create_default_context()
        if self.skip_tls_verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        smtp.starttls(context=ctx)
        smtp.ehlo()
        smtp.login(self.user, self.password)
        return smtp


class ZulipProtocol(types_dynamic.AbstractKindModel):
    KIND = "zulip"

    endpoint = properties.property(
        types.Url(),
        required=True,
    )
    email_address = properties.property(
        types.Email(),
        required=True,
    )
    api_key = properties.property(
        types.String(),
        required=True,
    )

    def send(self, content, user_context):
        client = zulip.Client(
            site=self.endpoint,
            email=self.email_address,
            api_key=self.api_key,
        )
        if content.KIND == RenderedStreamMessageContent.KIND:
            request = {
                "type": "stream",
                "to": content.to,
                "topic": content.topic,
                "content": content.content,
            }
        elif content.KIND == RenderedDirectMessageContent.KIND:
            request = {
                "type": "direct",
                "to": content.to,
                "content": content.content,
            }
        else:
            raise NotImplementedError(f"Unsupported content type {content}")
        result = client.send_message(request)
        if result["result"] != "success":
            raise RuntimeError(f"Failed to send message: {result['msg']}")


class FirebaseProtocol(types_dynamic.AbstractKindModel):
    KIND = "firebase"

    RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

    ttl_seconds = properties.property(
        types.Integer(min_value=1, max_value=3600),
        default=3600,
    )

    def validate_content(self, content):
        data_size = len(
            json.dumps(
                content.data,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        )
        if data_size > 4096:
            raise transports.DeliveryError(
                "firebase_payload_too_large",
                [
                    {
                        "device_uuid": str(content.device_uuid),
                        "code": "firebase_payload_too_large",
                    }
                ],
                permanent=True,
            )

    @staticmethod
    def delivery_error(content, code, permanent=False):
        return transports.DeliveryError(
            code,
            [{"device_uuid": str(content.device_uuid), "code": code}],
            permanent=permanent,
        )

    def get_session(self, content):
        try:
            return transports.firebase_session()
        except (
            OSError,
            ValueError,
            RuntimeError,
            google_auth_exceptions.GoogleAuthError,
        ):
            raise self.delivery_error(
                content,
                "firebase_credentials_error",
                permanent=True,
            ) from None

    def send(self, content, user_context, firebase=None):
        self.validate_content(content)

        session, project_id = firebase or self.get_session(content)
        try:
            response = session.post(
                f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send",
                json={
                    "message": {
                        "token": content.registration_token,
                        "data": content.data,
                        "android": {
                            "priority": content.priority,
                            "ttl": f"{self.ttl_seconds}s",
                        },
                    }
                },
                timeout=10,
            )
        except google_auth_exceptions.RefreshError as error:
            raise self.delivery_error(
                content,
                "firebase_auth_error",
                permanent=not error.retryable,
            ) from None
        except (requests.RequestException, google_auth_exceptions.TransportError):
            raise self.delivery_error(content, "firebase_transport_error") from None
        except google_auth_exceptions.GoogleAuthError:
            raise self.delivery_error(
                content,
                "firebase_auth_error",
                permanent=True,
            ) from None
        if response.status_code == 200:
            return

        try:
            details = response.json().get("error", {}).get("details", [])
        except (AttributeError, ValueError):
            details = []
        if any(
            detail.get("@type") == "type.googleapis.com/google.firebase.fcm.v1.FcmError"
            and detail.get("errorCode") == "UNREGISTERED"
            for detail in details
            if isinstance(detail, dict)
        ):
            raise transports.DeliveryError(
                "registration_token_inactive",
                [
                    {
                        "device_uuid": str(content.device_uuid),
                        "code": "registration_token_inactive",
                    }
                ],
                permanent=True,
            )

        # Provider response bodies can echo input and must not reach logs or status.
        code = f"firebase_http_{response.status_code}"
        raise transports.DeliveryError(
            code,
            [{"device_uuid": str(content.device_uuid), "code": code}],
            permanent=response.status_code not in self.RETRYABLE_HTTP_STATUSES,
            retry_after=transports.retry_after_seconds(response),
        )


class ApnsProtocol(types_dynamic.AbstractKindModel):
    KIND = "apns"

    topic = properties.property(
        types.String(min_length=1, max_length=255), required=True
    )

    def send(self, content, user_context):
        apns.send(self.topic, content)


class NotificationRelayProtocol(types_dynamic.AbstractKindModel):
    KIND = "notification_relay"

    transport = properties.property(types.Enum(["fcm", "apns"]), default="fcm")

    def send(self, content, user_context, destination):
        client, event_type, project, _ = destination
        event_id = str(content.event_uuid)
        device_id = str(content.device_uuid)
        try:
            try:
                remote = client.get("/v1/events/", event_id)
            except http_exceptions.NotFoundError:
                params = {
                    "device_uuid": device_id,
                    "registration_token": content.registration_token,
                    "priority": content.priority,
                }
                if self.transport == "apns":
                    params.update(
                        payload=json.dumps(
                            content.payload, ensure_ascii=False, separators=(",", ":")
                        ),
                        apns_environment=content.environment,
                        apns_collapse_id=content.collapse_id,
                    )
                else:
                    params["payload"] = content.data["payload"]
                try:
                    client.create(
                        "/v1/events/",
                        data={
                            "uuid": event_id,
                            "project_id": project,
                            "event_type": f"/v1/event_types/{event_type}",
                            "exchange": {"kind": "System"},
                            "event_params": params,
                            "last_retry_at": content.expires_at.isoformat(),
                        },
                    )
                except http_exceptions.ConflictError:
                    # A previous submission may have committed before its reply arrived.
                    pass
                raise transports.DeliveryPending() from None
        except (http_exceptions.ForbiddenError, http_exceptions.UnauthorizedError):
            raise transports.DeliveryError(
                "relay_access_denied", permanent=True
            ) from None
        except (transports.DeliveryError, transports.DeliveryPending):
            raise
        except (
            http_exceptions.BaseHTTPException,
            requests.exceptions.RequestException,
            KeyError,
            TypeError,
            ValueError,
        ):
            raise transports.DeliveryError("relay_transport_error") from None

        status = remote.get("status")
        if status == StatusMixin.STATUS.ACTIVE.value:
            return
        if status != StatusMixin.STATUS.ERROR.value:
            raise transports.DeliveryPending()
        errors = remote.get("errors", [])
        if any(
            error.get("code") == "registration_token_inactive"
            and error.get("device_uuid") == device_id
            and error.get("permanent") is True
            for error in errors
        ):
            raise transports.DeliveryError(
                "registration_token_inactive",
                [{"code": "registration_token_inactive", "device_uuid": device_id}],
                permanent=True,
            )
        if errors and all(error.get("permanent") is True for error in errors):
            raise transports.DeliveryError("relay_upstream_rejected", permanent=True)
        raise transports.DeliveryPending()


class FirebaseProtocolType(types_dynamic.KindModelSelectorType):
    """Read the obsolete provider shape without honoring its credential path."""

    def from_simple_type(self, value):
        if isinstance(value, dict) and value.get("kind") == FirebaseProtocol.KIND:
            value = value.copy()
            value.pop("credentials_file", None)
            value.pop("firebase_project_id", None)
        return super().from_simple_type(value)


class InactiveToken(
    models.ModelWithUUID, models.ModelWithTimestamp, orm.SQLStorableMixin
):
    __tablename__ = "inactive_tokens"

    scope = properties.property(types.String(max_length=4096), required=True)
    token_fingerprint = properties.property(
        types.String(min_length=64, max_length=64), required=True
    )

    @classmethod
    def identify(cls, scope, token):
        fingerprint = hashlib.sha256(token.encode()).hexdigest()
        return cls(
            uuid=sys_uuid.uuid5(sys_uuid.NAMESPACE_URL, f"{scope}:{fingerprint}"),
            scope=scope,
            token_fingerprint=fingerprint,
        )


class Provider(
    models.ModelWithUUID,
    models.ModelWithRequiredNameDesc,
    ModelWithAlwaysActiveStatus,
    models.ModelWithProject,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
    ua_models.TargetResourceMixin,
):
    __tablename__ = "providers"

    protocol = properties.property(
        FirebaseProtocolType(
            types_dynamic.KindModelType(SimpleSmtpProtocol),
            types_dynamic.KindModelType(StartTlsSmtpProtocol),
            types_dynamic.KindModelType(ZulipProtocol),
            types_dynamic.KindModelType(FirebaseProtocol),
            types_dynamic.KindModelType(ApnsProtocol),
            types_dynamic.KindModelType(NotificationRelayProtocol),
        ),
        required=True,
    )

    def send(self, content, user_context):
        if isinstance(self.protocol, FirebaseProtocol):
            self.protocol.validate_content(content)
            firebase = self.protocol.get_session(content)
            scope = f"firebase:{firebase[1]}"
            transport = {"firebase": firebase}
        elif isinstance(self.protocol, NotificationRelayProtocol):
            destination = transports.relay_destination(self.protocol.transport)
            # APNs can accept the same token after the app registers again.
            if self.protocol.transport == "apns":
                return self.protocol.send(
                    content=content, user_context=user_context, destination=destination
                )
            scope = destination[3]
            transport = {"destination": destination}
        else:
            return self.protocol.send(content=content, user_context=user_context)

        inactive = InactiveToken.identify(scope, content.registration_token)
        if InactiveToken.objects.get_one_or_none(
            filters={"uuid": filters.EQ(inactive.uuid)}
        ):
            raise transports.DeliveryError(
                "registration_token_inactive",
                [
                    {
                        "device_uuid": str(content.device_uuid),
                        "code": "registration_token_inactive",
                        "permanent": True,
                    }
                ],
                permanent=True,
            )
        try:
            return self.protocol.send(
                content=content,
                user_context=user_context,
                **transport,
            )
        except transports.DeliveryError as error:
            if any(
                item.get("code") == "registration_token_inactive"
                for item in error.errors
            ):
                inactive.insert()
            raise


class EventType(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    ModelWithAlwaysActiveStatus,
    models.ModelWithProject,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
    ua_models.TargetResourceMixin,
):
    __tablename__ = "event_types"


class AbstractContent(types_dynamic.AbstractKindModel):
    requires_user_context = True

    def get_id(self):
        return self.__class__.__name__.lower()


class RenderedEmailContent(AbstractContent):
    KIND = "rendered_email"

    recipient = properties.property(
        types.AllowNone(types.Email()),
        default=None,
    )

    title = properties.property(
        types.String(max_length=256),
        default="",
    )
    bodies = properties.property(
        types.List(),
        default=list,
    )


class EmailContent(RenderedEmailContent):
    KIND = "email"

    # Only template authors can opt into an explicit recipient. Event parameters
    # alone must never override the exchange's delivery address.
    recipient = properties.property(
        types.AllowNone(types.String(max_length=256)),
        default=None,
    )

    def render(self, params):
        return RenderedEmailContent(
            recipient=(
                jinja2.Template(
                    self.recipient, undefined=jinja2.StrictUndefined
                ).render(**params)
                if self.recipient is not None
                else None
            ),
            title=jinja2.Template(self.title).render(**params),
            bodies=[jinja2.Template(body).render(**params) for body in self.bodies],
        )


class RenderedStreamMessageContent(AbstractContent):
    KIND = "rendered_zulip_stream_message"

    to = properties.property(
        types.String(min_length=1, max_length=256),
        required=True,
    )
    topic = properties.property(
        types.String(min_length=1, max_length=256),
        required=True,
    )
    content = properties.property(
        types.String(min_length=1, max_length=10000),
        required=True,
    )


class ZulipStreamMessageContent(RenderedStreamMessageContent):
    KIND = "zulip_stream_message"

    to = properties.property(
        types.String(min_length=1, max_length=256),
        default="{{ channel }}",
    )
    topic = properties.property(
        types.String(min_length=1, max_length=256),
        default="{{ topic }}",
    )
    content = properties.property(
        types.String(min_length=1, max_length=10000),
        default="{{ message }}",
    )

    def render(self, params):
        return RenderedStreamMessageContent(
            to=jinja2.Template(self.to).render(**params),
            topic=jinja2.Template(self.topic).render(**params),
            content=jinja2.Template(self.content).render(**params),
        )


class RenderedDirectMessageContent(AbstractContent):
    KIND = "rendered_zulip_direct_message"

    to = properties.property(
        types.TypedList(nested_type=types.String(min_length=1, max_length=256)),
        required=True,
    )
    content = properties.property(
        types.String(min_length=1, max_length=10000),
        required=True,
    )


class ZulipDirectMessageContent(RenderedDirectMessageContent):
    KIND = "zulip_direct_message"

    to = properties.property(
        types.String(min_length=1, max_length=256),
        default="{{ users }}",
    )
    content = properties.property(
        types.String(min_length=1, max_length=10000),
        default="{{ message }}",
    )

    def render(self, params):
        return RenderedDirectMessageContent(
            to=[
                user.strip()
                for user in jinja2.Template(self.to).render(**params).split(",")
            ],
            content=jinja2.Template(self.content).render(**params),
        )


ZULIP_MESSAGE_TYPE_MAP = {
    RenderedStreamMessageContent.KIND: "stream",
    RenderedDirectMessageContent.KIND: "direct",
}


class RenderedFirebaseAndroidContent(AbstractContent):
    KIND = "rendered_firebase_android"
    requires_user_context = False

    device_uuid = properties.property(
        types.UUID(),
        required=True,
    )
    registration_token = properties.property(
        types.String(min_length=1, max_length=4096),
        required=True,
    )
    data = properties.property(
        types.Dict(),
        required=True,
    )
    priority = properties.property(
        types.Enum(["normal", "high"]),
        default="normal",
    )


class FirebaseAndroidContent(AbstractContent):
    KIND = "firebase_android"
    requires_user_context = False

    device_uuid = properties.property(
        types.String(min_length=1, max_length=64),
        default="{{ device_uuid }}",
    )
    registration_token = properties.property(
        types.String(min_length=1, max_length=4096),
        default="{{ registration_token }}",
    )
    data = properties.property(
        types.Dict(),
        required=True,
    )
    priority = properties.property(
        types.String(min_length=1, max_length=64),
        default="{{ priority | default('normal') }}",
    )

    def render(self, params):
        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in self.data.items()
        ):
            raise exceptions.ValidationErrorException()
        return RenderedFirebaseAndroidContent(
            device_uuid=sys_uuid.UUID(
                jinja2.Template(self.device_uuid).render(**params)
            ),
            registration_token=jinja2.Template(self.registration_token).render(
                **params
            ),
            data={
                key: jinja2.Template(value).render(**params)
                for key, value in self.data.items()
            },
            priority=jinja2.Template(self.priority).render(**params),
        )


class RenderedApnsContent(AbstractContent):
    KIND = "rendered_apns"
    requires_user_context = False

    device_uuid = properties.property(types.UUID(), required=True)
    event_uuid = properties.property(types.UUID(), required=True)
    expires_at = properties.property(types.UTCDateTimeZ(), required=True)
    registration_token = properties.property(
        types.String(min_length=1, max_length=4096), required=True
    )
    payload = properties.property(types.Dict(), required=True)
    environment = properties.property(types.Enum(list(apns.ENDPOINTS)), required=True)
    collapse_id = properties.property(
        types.String(min_length=64, max_length=64), required=True
    )
    priority = properties.property(types.Enum(["normal", "high"]), default="normal")


class ApnsContent(AbstractContent):
    KIND = "apns"
    requires_user_context = False

    def render(self, params, *, event):
        return RenderedApnsContent(
            device_uuid=sys_uuid.UUID(params["device_uuid"]),
            event_uuid=event.uuid,
            expires_at=event.last_retry_at,
            registration_token=params["registration_token"],
            payload=json.loads(params["payload"]),
            environment=params["apns_environment"],
            collapse_id=params["apns_collapse_id"],
            priority=params["priority"],
        )


class RenderedNotificationRelayContent(RenderedFirebaseAndroidContent):
    KIND = "rendered_notification_relay"

    event_uuid = properties.property(types.UUID(), required=True)
    expires_at = properties.property(types.UTCDateTimeZ(), required=True)


class NotificationRelayContent(AbstractContent):
    KIND = "notification_relay"
    requires_user_context = False

    def render(self, params, *, event):
        return RenderedNotificationRelayContent(
            event_uuid=event.uuid,
            expires_at=event.last_retry_at,
            device_uuid=sys_uuid.UUID(str(params["device_uuid"])),
            registration_token=params["registration_token"],
            data={"payload": params["payload"]},
            priority=params.get("priority", "normal"),
        )


class LegacyMobilePushContent(AbstractContent):
    """Keep the preference carried by an existing mobile push binding."""

    KIND = "mobile_push"
    requires_user_context = False


class Template(
    models.ModelWithUUID,
    models.ModelWithRequiredNameDesc,
    ModelWithAlwaysActiveStatus,
    models.ModelWithProject,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
    ua_models.TargetResourceMixin,
):
    __tablename__ = "templates"

    content = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(EmailContent),
            types_dynamic.KindModelType(ZulipStreamMessageContent),
            types_dynamic.KindModelType(ZulipDirectMessageContent),
            types_dynamic.KindModelType(FirebaseAndroidContent),
            types_dynamic.KindModelType(ApnsContent),
            types_dynamic.KindModelType(NotificationRelayContent),
            types_dynamic.KindModelType(LegacyMobilePushContent),
        ),
        required=True,
    )
    params = properties.property(
        types.Dict(),
        required=True,
    )
    provider = relationships.relationship(
        Provider,
        required=True,
        prefetch=True,
    )
    event_type = relationships.relationship(
        EventType,
        required=True,
    )
    is_default = properties.property(
        types.Boolean(),
        default=False,
    )


class UserExchange(types_dynamic.AbstractKindModel):
    KIND = "User"

    user_uuid = properties.property(
        types.UUID(),
        required=True,
    )

    def get_context(self, iam_client):
        return {
            "user": iam_client.get_user(self.user_uuid),
        }


class UserExchangeType(types_dynamic.KindModelType):
    def __init__(self):
        super().__init__(UserExchange)

    def from_simple_type(self, value):
        value = value.copy()
        if "user_id" in value:
            legacy_user = value.pop("user_id")
            if "user_uuid" in value and value["user_uuid"] != legacy_user:
                raise exceptions.ParseError(value="Conflicting user identifiers")
            value["user_uuid"] = legacy_user
        return super().from_simple_type(value)

    def to_simple_type(self, value):
        result = super().to_simple_type(value)
        # Keep the original v1 response key for clients reading existing User events.
        result["user_id"] = result["user_uuid"]
        return result


class DummyExchange(types_dynamic.AbstractKindModel):
    KIND = "Dummy"

    def get_context(self, iam_client):
        return {}


class ProjectExchange(types_dynamic.AbstractKindModel):
    KIND = "Project"

    project_id = properties.property(
        types.UUID(),
        required=True,
    )


class SystemExchange(types_dynamic.AbstractKindModel):
    KIND = "System"


class Binding(
    models.ModelWithUUID,
    models.ModelWithProject,
    ModelWithAlwaysActiveStatus,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    __tablename__ = "bindings"

    user = properties.property(
        types.UUID(),
        required=True,
    )
    template = relationships.relationship(
        Template,
        required=True,
    )
    event_type = relationships.relationship(
        EventType,
        required=True,
    )

    def validate(self):
        super().validate()
        if (
            self.template.project_id != self.project_id
            and not self.template.is_default
            or self.template.event_type.uuid != self.event_type.uuid
        ):
            raise exceptions.ValidationErrorException()


class StatusMixin(models.Model):
    next_retry_delta = 60  # 60 sec
    initial_transport_retry_delta = 5  # 5 sec
    last_retry_delta = 1 * 24 * 60 * 60  # 1 day

    STATUS = c.EventStatus

    status = properties.property(
        types.Enum([s.value for s in STATUS]),
        default=STATUS.NEW.value,
    )
    status_description = properties.property(
        types.String(max_length=255),
        default="",
    )
    errors = properties.property(types.TypedList(types.Dict()), default=list)
    next_retry_at = properties.property(
        types.UTCDateTimeZ(),
        default=next_time(seconds=0),  # now + 0sec
    )
    last_retry_at = properties.property(
        types.UTCDateTimeZ(),
        default=next_time(seconds=last_retry_delta),  # now + 1day
    )
    retry_count = properties.property(
        types.Integer(min_value=0, max_value=65536),
        default=0,
    )

    def _get_prepared_data(self, properties=None):
        data = super()._get_prepared_data(properties)
        if "errors" in data:
            # A Python list otherwise binds as a PostgreSQL array, not JSONB.
            data["errors"] = sql_json.Jsonb(data["errors"])
        return data

    def reset_next_retry(self, seconds=None):
        now = datetime.datetime.now(datetime.timezone.utc)
        delay = self.next_retry_delta if seconds is None else seconds
        self.next_retry_at = now + datetime.timedelta(seconds=delay)

    def set_error_status(self, error_message, retry_after=None):
        self.status_description = str(error_message)
        self.retry_count += 1
        self.status = self.STATUS.ERROR.value
        self.reset_next_retry(retry_after)
        self.save()

    def set_done_status(self):
        self.status_description = "ok"
        self.status = self.STATUS.ACTIVE.value
        self.errors = []


class Event(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithProject,
    models.ModelWithTimestamp,
    StatusMixin,
    orm.SQLStorableMixin,
):
    __tablename__ = "events"

    exchange = properties.property(
        types_dynamic.KindModelSelectorType(
            UserExchangeType(),
            types_dynamic.KindModelType(ProjectExchange),
            types_dynamic.KindModelType(SystemExchange),
            types_dynamic.KindModelType(DummyExchange),
        ),
        default=DummyExchange,
    )
    event_params = properties.property(
        types.Dict(),
        required=True,
    )
    event_type = relationships.relationship(
        EventType,
        required=True,
    )

    def get_context(self, iam_client, templates=None):
        templates = self.get_templates() if templates is None else templates
        if any(template.content.requires_user_context for template in templates):
            return self.exchange.get_context(iam_client)
        return {}

    def render(self, iam_client):
        templates = self.get_templates()
        context = self.get_context(iam_client, templates)

        rendered_events = []
        for template in templates:
            params = context.copy()
            params.update(self.event_params)
            render_options = (
                {"event": self}
                if isinstance(template.content, (NotificationRelayContent, ApnsContent))
                else {}
            )
            rendered_content = template.content.render(params, **render_options)
            rendered_events.append(
                RenderedEvent(
                    content=rendered_content,
                    event_id=self.uuid,
                    provider=template.provider,
                    user_context=context,
                    status=StatusMixin.STATUS.IN_PROGRESS.value,
                )
            )

        return rendered_events

    def get_templates(self):
        selected = {}
        has_retired_push = False
        push_contents = (FirebaseAndroidContent, NotificationRelayContent, ApnsContent)
        if isinstance(self.exchange, UserExchange):
            bindings = Binding.objects.get_all(
                filters={
                    "project_id": filters.EQ(self.project_id),
                    "event_type": filters.EQ(self.event_type),
                    "user": filters.EQ(self.exchange.user_uuid),
                }
            )
            selected = {
                binding.template.uuid: binding.template
                for binding in bindings
                if not isinstance(binding.template.content, LegacyMobilePushContent)
            }
            has_retired_push = any(
                isinstance(binding.template.content, LegacyMobilePushContent)
                for binding in bindings
            )
            has_current_push = any(
                isinstance(template.content, push_contents)
                for template in selected.values()
            )
            if selected and (not has_retired_push or has_current_push):
                return list(selected.values())
        defaults = Template.objects.get_all(
            filters={
                "event_type": filters.EQ(self.event_type),
                "is_default": filters.EQ(True),
            }
        )
        if has_retired_push:
            return list(selected.values()) + [
                template
                for template in defaults
                if isinstance(template.content, push_contents)
            ]
        return [
            template
            for template in defaults
            if not isinstance(template.content, LegacyMobilePushContent)
        ]


class RenderedEvent(
    models.ModelWithUUID,
    models.ModelWithTimestamp,
    StatusMixin,
    orm.SQLStorableMixin,
):
    __tablename__ = "rendered_events"

    content = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(RenderedEmailContent),
            types_dynamic.KindModelType(RenderedStreamMessageContent),
            types_dynamic.KindModelType(RenderedDirectMessageContent),
            types_dynamic.KindModelType(RenderedFirebaseAndroidContent),
            types_dynamic.KindModelType(RenderedApnsContent),
            types_dynamic.KindModelType(RenderedNotificationRelayContent),
        ),
        required=True,
    )
    event_id = properties.property(
        types.UUID(),
        required=True,
    )
    provider = relationships.relationship(
        Provider,
        required=True,
        prefetch=True,
    )
    user_context = properties.property(
        types.Dict(),
        required=True,
    )

    def send(self):
        try:
            self.provider.send(
                content=self.content,
                user_context=self.user_context,
            )
        except transports.DeliveryPending:
            self.status = self.STATUS.IN_PROGRESS.value
            self.status_description = ""
            self.errors = []
            self.reset_next_retry(seconds=self.initial_transport_retry_delta)
            self.update()
            return
        except transports.DeliveryError as error:
            self.errors = error.errors or [{"code": error.code}]
            self.errors = [
                dict(item, permanent=item.get("permanent", error.permanent))
                for item in self.errors
            ]
            retry_after = error.retry_after
            if retry_after is None and not error.permanent:
                retry_after = min(
                    self.initial_transport_retry_delta
                    * (2 ** min(self.retry_count, 4)),
                    self.next_retry_delta,
                )
            self.set_error_status(error.code, retry_after=retry_after)
            if error.permanent:
                self.next_retry_at = self.last_retry_at + datetime.timedelta(days=365)
                self.update()
            return
        except Exception as e:
            LOG.exception("Failed to send event")
            self.set_error_status(e)
            return
        self.set_done_status()
        self.update()


class UnprocessedEvent(
    models.ModelWithUUID,
    orm.SQLStorableMixin,
):
    __tablename__ = "unprocessed_events"
    event = relationships.relationship(
        Event,
        required=True,
        prefetch=True,
    )
    next_retry_at = properties.property(
        types.UTCDateTimeZ(),
        required=True,
    )
    last_retry_at = properties.property(
        types.UTCDateTimeZ(),
        required=True,
    )

    def process_event(self, iam_client) -> int | None:
        try:
            rendered_events = self.event.render(iam_client)
        except Exception as e:
            LOG.exception("Failed to render event")
            self.event.set_error_status(e)
            return None
        for rendered_event in rendered_events:
            rendered_event.insert()
        return len(rendered_events)


class IncorrectStatuses(
    models.ModelWithUUID,
    orm.SQLStorableMixin,
):
    __tablename__ = "incorrect_statuses"

    STATUS = c.EventStatus

    event = relationships.relationship(
        Event,
        required=True,
        prefetch=True,
    )
    user_status = properties.property(
        types.Enum([s.value for s in STATUS]),
        required=True,
    )
    user_status_description = properties.property(
        types.String(),
        default="",
    )
    system_status = properties.property(
        types.Enum([s.value for s in STATUS]),
        required=True,
    )
    system_status_description = properties.property(
        types.String(),
        default="",
    )
    system_errors = properties.property(types.TypedList(types.Dict()), default=list)
    user_errors = properties.property(types.TypedList(types.Dict()), default=list)
