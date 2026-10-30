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
import hashlib
import json
import logging
import smtplib
import ssl
import uuid as sys_uuid
from email.mime import multipart, text

import jinja2
import zulip
from gcl_sdk.agents.universal.dm import models as ua_models
from psycopg.types import json as sql_json
from restalchemy.common import exceptions
from restalchemy.dm import (
    filters,
    models,
    properties,
    relationships,
    types,
    types_dynamic,
)
from restalchemy.storage.sql import orm

from exordos_notification.common import constants as c
from exordos_notification.common import push, transports
from exordos_notification.dm import notification

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
        msg["To"] = user_context["user"]["email"]
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
                to_addrs=user_context["user"]["email"],
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

    firebase_project_id = properties.property(types.String(min_length=1), required=True)
    credentials_file = properties.property(types.String(min_length=1), required=True)
    ttl_seconds = properties.property(
        types.Integer(min_value=1, max_value=3600), default=3600
    )

    def send(self, content, user_context):
        now = datetime.datetime.now(datetime.timezone.utc)
        ttl = min(self.ttl_seconds, int((content.expires_at - now).total_seconds()))
        if ttl <= 0:
            raise transports.DeliveryError("expired", permanent=True)
        data = {
            "payload": json.dumps(
                content.payload, ensure_ascii=False, separators=(",", ":")
            ),
        }
        if (
            len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())
            > 4096
        ):
            raise transports.DeliveryError("payload_too_large", permanent=True)
        request = {
            "message": {
                "token": content.registration_token,
                "data": data,
                "android": {
                    "priority": (
                        content.priority
                        if content.payload["kind"] == c.NOTIFICATION_TOPIC_UPDATE
                        else "high"
                        if content.payload["kind"] == c.NOTIFICATION_MESSAGE_CREATE
                        else "normal"
                    ),
                    "ttl": f"{ttl}s",
                },
            },
        }
        session = transports.firebase_session(self.credentials_file)
        response = session.post(
            f"https://fcm.googleapis.com/v1/projects/{self.firebase_project_id}/messages:send",
            json=request,
            timeout=10,
        )
        if response.status_code == 200:
            return
        details = response.json().get("error", {}).get("details", [])
        unregistered = any(
            detail.get("@type") == "type.googleapis.com/google.firebase.fcm.v1.FcmError"
            and detail.get("errorCode") == "UNREGISTERED"
            for detail in details
        )
        if unregistered:
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
        # Do not persist or log provider response bodies, which may echo input data.
        raise transports.DeliveryError(
            f"fcm_http_{response.status_code}",
            permanent=400 <= response.status_code < 500 and response.status_code != 429,
        )


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

    def remember(self):
        # Concurrent failures for the same token must not abort a delivery transaction.
        with self._get_engine().session_manager() as session:
            session.execute(
                "INSERT INTO inactive_tokens (uuid, scope, token_fingerprint) "
                "VALUES (%s, %s, %s) ON CONFLICT (uuid) DO NOTHING",
                (str(self.uuid), self.scope, self.token_fingerprint),
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
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(SimpleSmtpProtocol),
            types_dynamic.KindModelType(StartTlsSmtpProtocol),
            types_dynamic.KindModelType(ZulipProtocol),
            types_dynamic.KindModelType(FirebaseProtocol),
        ),
        required=True,
    )

    def send(self, content, user_context):
        if isinstance(self.protocol, FirebaseProtocol):
            scope = f"firebase:{self.protocol.firebase_project_id}"
            targets = [
                {
                    "device_uuid": str(content.device_uuid),
                    "registration_token": content.registration_token,
                }
            ]
        else:
            return self.protocol.send(content=content, user_context=user_context)

        active, inactive = [], []
        for target in targets:
            key = InactiveToken.identify(scope, target["registration_token"])
            if InactiveToken.objects.get_one_or_none(
                filters={"uuid": filters.EQ(key.uuid)}
            ):
                inactive.append(
                    {
                        "device_uuid": target["device_uuid"],
                        "code": "registration_token_inactive",
                        "permanent": True,
                    }
                )
            else:
                active.append(target)
        if not active:
            raise transports.DeliveryError(
                "registration_token_inactive", inactive, permanent=True
            )
        try:
            self.protocol.send(content=content, user_context=user_context)
        except transports.DeliveryError as error:
            rejected = {
                item["device_uuid"]
                for item in error.errors
                if item["code"] == "registration_token_inactive"
            }
            for target in targets:
                if target["device_uuid"] in rejected:
                    key = InactiveToken.identify(scope, target["registration_token"])
                    key.remember()
            # Polling the same upstream event can report an already cached rejection.
            errors = error.errors + [
                item for item in inactive if item not in error.errors
            ]
            raise transports.DeliveryError(
                error.code,
                errors,
                error.permanent,
            ) from None
        if inactive:
            raise transports.DeliveryError(
                "registration_token_inactive", inactive, permanent=True
            )


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

    def render(self, params):
        return RenderedEmailContent(
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


class RenderedMobilePushContent(AbstractContent):
    KIND = "rendered_mobile_push"

    device_uuid = properties.property(types.UUID(), required=True)
    registration_token = properties.property(types.String(min_length=1), required=True)
    payload = properties.property(types.Dict(), required=True)
    expires_at = properties.property(types.UTCDateTimeZ(), required=True)
    priority = properties.property(types.Enum(["normal", "high"]), default="normal")


class MobilePushContent(AbstractContent):
    KIND = "mobile_push"
    requires_user_context = False

    def render(self, params):
        event = params["_notification_event"]
        route = {
            "uuid": event["uuid"],
            "user_uuid": event["exchange"]["user_uuid"],
            "message_uuid": event["event_params"].get("message_uuid"),
            **{
                field: event["event_params"][field]
                for field in (
                    "kind",
                    "organization_uuid",
                    "project_uuid",
                    "stream_uuid",
                    "topic_uuid",
                )
            },
        }
        result = []
        for device in event["event_params"]["devices"]:
            if device["content"]["kind"] != "encrypted":
                raise exceptions.ValidationErrorException()
            result.append(
                RenderedMobilePushContent(
                    device_uuid=sys_uuid.UUID(device["device_uuid"]),
                    registration_token=device["registration_token"],
                    payload={
                        **route,
                        "device_uuid": device["device_uuid"],
                        "content": device["content"],
                    },
                    expires_at=params["_notification_expires_at"],
                    priority=event["event_params"].get(
                        "priority",
                        "high"
                        if event["event_params"]["kind"]
                        == "notification_message_create"
                        else "normal",
                    ),
                )
            )
        return result


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
            types_dynamic.KindModelType(MobilePushContent),
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

    def reset_next_retry(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        self.next_retry_at = now + datetime.timedelta(seconds=self.next_retry_delta)

    def set_error_status(self, error_message):
        self.status_description = str(error_message)
        self.retry_count += 1
        self.status = self.STATUS.ERROR.value
        self.reset_next_retry()
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

    def validate(self):
        super().validate()
        if not push.is_push_event(self.event_type, self.event_params):
            return
        try:
            params = notification.PARAMETERS.from_simple_type(self.event_params)
        except (exceptions.ParseError, ValueError, TypeError):
            raise exceptions.ValidationErrorException() from None
        devices = [device.device_uuid for device in params.devices]
        if not isinstance(self.exchange, UserExchange):
            raise exceptions.ValidationErrorException()
        if c.PUSH_EVENT_TYPES[params.kind] != self.event_type.uuid:
            raise exceptions.ValidationErrorException()
        if params.project_uuid != self.project_id:
            raise exceptions.ValidationErrorException()
        if not devices or len(devices) != len(set(devices)):
            raise exceptions.ValidationErrorException()

    def get_context(self, iam_client):
        return self.exchange.get_context(iam_client)

    def render(self, iam_client):
        templates = self.get_templates()
        context = (
            self.get_context(iam_client)
            if any(t.content.requires_user_context for t in templates)
            else {}
        )

        rendered_events = []
        for template in templates:
            params = context.copy()
            params.update(self.event_params)
            params["_notification_event"] = self.notification_event()
            params["_notification_expires_at"] = self.notification_expires_at()
            contents = template.content.render(params)
            if not isinstance(contents, list):
                contents = [contents]
            for content in contents:
                extra = {}
                if isinstance(content, RenderedMobilePushContent):
                    target = str(content.device_uuid)
                    extra["uuid"] = sys_uuid.uuid5(
                        self.uuid, f"{template.uuid}:{target}"
                    )
                rendered_events.append(
                    RenderedEvent(
                        **extra,
                        content=content,
                        event_id=self.uuid,
                        provider=template.provider,
                        user_context=context,
                        status=StatusMixin.STATUS.IN_PROGRESS.value,
                    )
                )

        return rendered_events

    def notification_expires_at(self):
        return min(self.last_retry_at, self.created_at + datetime.timedelta(hours=1))

    def notification_event(self):
        return {
            "uuid": str(self.uuid),
            "project_id": str(self.project_id),
            "exchange": self.properties["exchange"].property_type.to_simple_type(
                self.exchange
            ),
            "event_type": f"/v1/event_types/{self.event_type.uuid}",
            "event_params": self.event_params,
            "last_retry_at": self.notification_expires_at()
            .isoformat()
            .replace("+00:00", "Z"),
        }

    def get_templates(self):
        apply_bindings = not push.is_push_event(
            self.event_type, self.event_params
        ) or push.use_local_bindings(self.event_params)
        if isinstance(self.exchange, UserExchange) and apply_bindings:
            bindings = Binding.objects.get_all(
                filters={
                    "project_id": filters.EQ(self.project_id),
                    "event_type": filters.EQ(self.event_type),
                    "user": filters.EQ(self.exchange.user_uuid),
                }
            )
            if bindings:
                return list(
                    {
                        binding.template.uuid: binding.template for binding in bindings
                    }.values()
                )
        return Template.objects.get_all(
            filters={
                "event_type": filters.EQ(self.event_type),
                "is_default": filters.EQ(True),
            }
        )


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
            types_dynamic.KindModelType(RenderedMobilePushContent),
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
        except transports.DeliveryError as error:
            self.errors = error.errors
            if not self.errors:
                item = {"code": error.code}
                if isinstance(self.content, RenderedMobilePushContent):
                    item["device_uuid"] = str(self.content.device_uuid)
                self.errors = [item]
            self.errors = [
                dict(item, permanent=item.get("permanent", error.permanent))
                for item in self.errors
            ]
            self.set_error_status(error.code)
            if error.permanent:
                self.next_retry_at = self.last_retry_at
                self.update()
            return
        except Exception as e:
            if isinstance(self.content, RenderedMobilePushContent):
                LOG.warning("Transport request failed for event %s", self.event_id)
                self.errors = [{"code": "transport_error", "permanent": False}]
                self.set_error_status("transport_error")
            else:
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

    def process_event(self, iam_client):
        try:
            rendered_events = self.event.render(iam_client)
        except Exception as e:
            LOG.exception("Failed to render event")
            self.event.set_error_status(e)
            return
        for rendered_event in rendered_events:
            rendered_event.insert()


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
