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
import json
import logging
from dataclasses import dataclass
from email.mime import text
from email.mime import multipart
import smtplib

import jinja2
from restalchemy.dm import filters
from restalchemy.dm import models
from restalchemy.dm import properties
from restalchemy.dm import relationships
from restalchemy.dm import types
from restalchemy.dm import types_dynamic
from restalchemy.storage.sql import orm
import zulip

import firebase_admin
from firebase_admin import credentials, messaging
from firebase_admin.messaging import (
    UnregisteredError,
    SenderIdMismatchError,
    ThirdPartyAuthError,
)

from genesis_notification.common import constants as c
from genesis_notification.common.constants import PushDeliveryStatus

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


class UserDevice(
    models.ModelWithUUID,
    models.ModelWithProject,
    ModelWithAlwaysActiveStatus,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    __tablename__ = "user_devices"

    user_uuid = properties.property(
        types.UUID(),
        required=True,
    )

    app_id = properties.property(
        types.String(min_length=1, max_length=128),
        required=True,
    )

    provider_id = properties.property(
        types.UUID(),
        required=True,
    )

    fcm_token = properties.property(
        types.String(min_length=16, max_length=512),
        required=True,
    )

    platform = properties.property(
        types.Enum(["android", "ios"]),
        required=True,
    )

    app_version = properties.property(
        types.String(max_length=64),
        default="",
    )

    os_version = properties.property(
        types.String(max_length=64),
        default="",
    )

    device_model = properties.property(
        types.String(max_length=128),
        default="",
    )


class UserNotificationRoute(
    models.ModelWithUUID,
    models.ModelWithProject,
    ModelWithAlwaysActiveStatus,
    orm.SQLStorableMixin,
):
    __tablename__ = "user_notification_routes"

    user_uuid = properties.property(
        types.UUID(),
        required=True,
    )

    event_type_uuid = properties.property(
        types.UUID(),
        required=True,
    )

    app_id = properties.property(
        types.String(min_length=1, max_length=128),
        required=True,
    )

    provider_id = properties.property(
        types.UUID(),
        required=True,
    )


class AppProviderMapping(
    models.ModelWithUUID,
    models.ModelWithProject,
    ModelWithAlwaysActiveStatus,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    __tablename__ = "app_provider_mappings"

    app_id = properties.property(
        types.String(min_length=1, max_length=128),
        required=True,
    )

    provider_id = properties.property(
        types.UUID(),
        required=True,
    )


class EventTypeNotificationTarget(
    models.ModelWithUUID,
    models.ModelWithProject,
    ModelWithAlwaysActiveStatus,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    __tablename__ = "event_type_notification_targets"

    event_type_uuid = properties.property(
        types.UUID(),
        required=True,
    )

    app_id = properties.property(
        types.String(min_length=1, max_length=128),
        required=True,
    )

    provider_id = properties.property(
        types.UUID(),
        required=True,
    )


FCM_PERMANENT_ERRORS = {
    "UNREGISTERED",
    "INVALID_ARGUMENT",
    "NOT_FOUND",
}

FCM_RETRYABLE_ERRORS = {
    "UNAVAILABLE",
    "INTERNAL",
    "QUOTA_EXCEEDED",
}


@dataclass
class PushDeliveryResult:

    user_device_uuid: str
    token: str

    status: PushDeliveryStatus

    error_code: str | None = None
    error_message: str | None = None

    provider_response: dict | None = None


@dataclass
class PushBatchResult:

    results: list[PushDeliveryResult]

    def success_count(self):
        return sum(1 for r in self.results if r.status == PushDeliveryStatus.SUCCESS)

    def permanent_failures(self):
        return [
            r for r in self.results
            if r.status == PushDeliveryStatus.PERMANENT_FAILURE
        ]

    def retryable_failures(self):
        return [
            r for r in self.results
            if r.status == PushDeliveryStatus.RETRYABLE_FAILURE
        ]

    def total_failure(self):
        return self.success_count() == 0


class FCMProtocol(types_dynamic.AbstractKindModel):
    KIND = "fcm"

    project_id = properties.property(
        types.String(),
        required=True,
    )

    service_account_json = properties.property(
        types.String(),
        required=True,
    )

    def _get_firebase_app(self):

        service_account_info = json.loads(self.service_account_json)

        cred = credentials.Certificate(service_account_info)

        app_name = f"fcm-{self.project_id}"

        try:
            app = firebase_admin.get_app(app_name)
        except ValueError:
            app = firebase_admin.initialize_app(
                cred,
                name=app_name,
            )

        return app

    def _map_exception(self, exc):

        if isinstance(exc, (
            UnregisteredError,
            SenderIdMismatchError,
        )):
            return PushDeliveryStatus.PERMANENT_FAILURE

        if isinstance(exc, (
            ThirdPartyAuthError,
        )):
            return PushDeliveryStatus.RETRYABLE_FAILURE

        return PushDeliveryStatus.RETRYABLE_FAILURE

    def _send_batch(self, user_devices, content):

        app = self._get_firebase_app()

        tokens = [i.fcm_token for i in user_devices]
        device_map = {
            i.fcm_token: i.uuid
            for i in user_devices
        }

        results = []

        for i in range(0, len(tokens), 500):

            chunk = tokens[i:i + 500]

            message = messaging.MulticastMessage(
                tokens=chunk,
                notification=messaging.Notification(
                    title=content.title,
                    body=content.body,
                ),
                data=content.data or {},
            )

            # In firebase-admin 6.x+, send_multicast was replaced with send_each_for_multicast
            response = messaging.send_each_for_multicast(message, app=app)

            for idx, resp in enumerate(response.responses):

                token = chunk[idx]
                user_device_uuid = device_map[token]

                if resp.success:

                    results.append(
                        PushDeliveryResult(
                            user_device_uuid=user_device_uuid,
                            token=token,
                            status=PushDeliveryStatus.SUCCESS,
                            provider_response={"message_id": resp.message_id},
                        )
                    )

                else:
                    status = self._map_exception(resp.exception)

                    results.append(
                        PushDeliveryResult(
                            user_device_uuid=user_device_uuid,
                            token=token,
                            status=status,
                            error_code=type(resp.exception).__name__,
                            error_message=str(resp.exception),
                        )
                    )

        return PushBatchResult(results)

    def _build_payload(self, token, content):

        return {
            "message": {
                "token": token,
                "notification": {
                    "title": content.title,
                    "body": content.body,
                },
                "data": content.data or {},
            }
        }

    def _process_batch_result(self, batch_result):

        for r in batch_result.permanent_failures():

            device = UserDevice.objects.get_one(
                filters={"uuid": r.user_device_uuid}
            )

            if device:
                device.status = c.AlwaysActiveStatus.INACTIVE.value
                device.save()

    def send(self, content, user_context, routing_context=None):

        if not routing_context:
            raise RuntimeError("Push routing context is required")

        provider_id = routing_context["provider_id"]
        user_devices = UserDevice.objects.get_all(
            filters={
                "project_id": filters.EQ(routing_context["project_id"]),
                "user_uuid": filters.EQ(routing_context["user_uuid"]),
                "app_id": filters.EQ(routing_context["app_id"]),
                "provider_id": filters.EQ(provider_id),
                "status": filters.EQ(c.AlwaysActiveStatus.ACTIVE.value),
            }
        )

        if not user_devices:
            return

        batch_result = self._send_batch(user_devices, content)

        self._process_batch_result(batch_result)

        if batch_result.total_failure():
            raise RuntimeError("Push delivery totally failed")


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

    @staticmethod
    def _resolve_recipient(content, user_context):
        recipient = getattr(content, "recipient", None)
        if recipient:
            return recipient
        return user_context["user"]["email"]

    def _build_message(self, content, user_context):
        msg = multipart.MIMEMultipart("alternative")
        msg["From"] = self.noreply_email_address
        msg["To"] = self._resolve_recipient(content, user_context)
        msg["Subject"] = content.title
        for body in content.bodies:
            msg.attach(text.MIMEText(body, "html", "utf-8"))
        return msg

    def _authenticate(self, smtp):
        return smtp

    def send(self, content, user_context, routing_context=None):
        msg = self._build_message(content, user_context)
        with smtplib.SMTP(self.host, self.port) as smtp:
            smtp = self._authenticate(smtp)
            return smtp.sendmail(
                from_addr=self.noreply_email_address,
                to_addrs=self._resolve_recipient(content, user_context),
                msg=msg.as_string(),
            )


class StartTlsSmtpProtocol(SimpleSmtpProtocol):
    KIND = "StartTlsSMTP"

    user = properties.property(
        types.Email(),
        required=True,
    )
    password = properties.property(
        types.String(max_length=128, min_length=1),
        required=True,
    )

    def _authenticate(self, smtp):
        smtp.ehlo()
        smtp.starttls()
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

    def send(self, content, user_context, routing_context=None):
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


class Provider(
    models.ModelWithUUID,
    models.ModelWithRequiredNameDesc,
    ModelWithAlwaysActiveStatus,
    models.ModelWithProject,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    __tablename__ = "providers"

    protocol = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(SimpleSmtpProtocol),
            types_dynamic.KindModelType(StartTlsSmtpProtocol),
            types_dynamic.KindModelType(ZulipProtocol),
            types_dynamic.KindModelType(FCMProtocol),
        ),
        required=True,
    )

    def send(self, content, user_context, routing_context=None):
        return self.protocol.send(
            content=content,
            user_context=user_context,
            routing_context=routing_context,
        )


class EventType(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    ModelWithAlwaysActiveStatus,
    models.ModelWithProject,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    __tablename__ = "event_types"


class AbstractContent(types_dynamic.AbstractKindModel):
    def get_id(self):
        return "%s" % self.__class__.__name__.lower()


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

    recipient = properties.property(
        types.AllowNone(types.String(max_length=512)),
        default="{{ user.email }}",
    )

    def render(self, params):
        rendered_recipient = (
            jinja2.Template(self.recipient).render(**params)
            if self.recipient else None
        )
        return RenderedEmailContent(
            recipient=rendered_recipient,
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


class RenderedPushContent(AbstractContent):
    KIND = "rendered_push"

    title = properties.property(types.String(), default="{{ title }}")
    body = properties.property(types.String(), default="{{ body }}")
    data = properties.property(types.Dict(), default=dict)


class PushContent(RenderedPushContent):
    KIND = "push"

    def render(self, params):
        return RenderedPushContent(
            title=jinja2.Template(self.title).render(**params),
            body=jinja2.Template(self.body).render(**params),
            data={
                k: jinja2.Template(v).render(**params) if isinstance(v, str) else v
                for k, v in self.data.items()
            },
        )


class Template(
    models.ModelWithUUID,
    models.ModelWithRequiredNameDesc,
    ModelWithAlwaysActiveStatus,
    models.ModelWithProject,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    __tablename__ = "templates"

    content = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(EmailContent),
            types_dynamic.KindModelType(ZulipStreamMessageContent),
            types_dynamic.KindModelType(ZulipDirectMessageContent),
            types_dynamic.KindModelType(PushContent),
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

    user_id = properties.property(
        types.UUID(),
        required=True,
    )

    def get_context(self, iam_client):
        return {
            "user": iam_client.get_user(self.user_id),
        }


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


class Event(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithProject,
    models.ModelWithTimestamp,
    StatusMixin,
    orm.SQLStorableMixin,
):
    __tablename__ = "events"

    app_id = properties.property(
        types.String(min_length=1, max_length=128),
        required=True,
    )

    exchange = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(UserExchange),
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

    def get_context(self, iam_client):
        return self.exchange.get_context(iam_client)

    def render(self, iam_client):
        context = self.get_context(iam_client)
        templates = Template.objects.get_all(
            filters={
                "event_type": filters.EQ(self.event_type),
                "is_default": filters.EQ(True),
            }
        )

        rendered_events = []
        for template in templates:
            params = context.copy()
            params.update(self.event_params)
            rendered_content = template.content.render(params)
            rendered_events.append(
                RenderedEvent(
                    content=rendered_content,
                    event_id=self.uuid,
                    project_id=self.project_id,
                    app_id=self.app_id,
                    event_type_uuid=self.event_type.uuid,
                    provider=template.provider,
                    user_context=context,
                    status=StatusMixin.STATUS.IN_PROGRESS.value,
                )
            )

        return rendered_events


class RenderedEvent(
    models.ModelWithUUID,
    models.ModelWithProject,
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
            types_dynamic.KindModelType(RenderedPushContent),
        ),
        required=True,
    )
    event_id = properties.property(
        types.UUID(),
        required=True,
    )
    app_id = properties.property(
        types.String(min_length=1, max_length=128),
        required=True,
    )
    event_type_uuid = properties.property(
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

    def _get_push_user_uuid(self):
        return self.user_context["user"]["uuid"]

    def _resolve_push_route(self):
        return UserNotificationRoute.objects.get_one_or_none(
            filters={
                "project_id": self.project_id,
                "user_uuid": self._get_push_user_uuid(),
                "event_type_uuid": self.event_type_uuid,
                "app_id": self.app_id,
                "status": c.AlwaysActiveStatus.ACTIVE.value,
            }
        )

    def _get_active_provider(self, provider_id):
        return Provider.objects.get_one_or_none(
            filters={
                "uuid": provider_id,
                "status": c.AlwaysActiveStatus.ACTIVE.value,
            }
        )

    def _validate_push_route(self, route):
        provider = self._get_active_provider(route.provider_id)
        if not provider:
            raise RuntimeError("Push route provider is inactive")
        if provider.protocol.KIND != FCMProtocol.KIND:
            raise RuntimeError("Push route provider must use FCM protocol")

        mapping = AppProviderMapping.objects.get_one_or_none(
            filters={
                "project_id": self.project_id,
                "app_id": self.app_id,
                "provider_id": route.provider_id,
                "status": c.AlwaysActiveStatus.ACTIVE.value,
            }
        )
        if not mapping:
            raise RuntimeError("Push route provider is not allowed for app_id")

        target = EventTypeNotificationTarget.objects.get_one_or_none(
            filters={
                "project_id": self.project_id,
                "event_type_uuid": self.event_type_uuid,
                "app_id": self.app_id,
                "provider_id": route.provider_id,
                "status": c.AlwaysActiveStatus.ACTIVE.value,
            }
        )
        if not target:
            raise RuntimeError("Push route provider is not allowed for event type")

        return provider

    def _send_push(self):
        route = self._resolve_push_route()
        if not route:
            LOG.info(
                "No active push route for event %s, user %s, event type %s",
                self.event_id,
                self._get_push_user_uuid(),
                self.event_type_uuid,
            )
            return

        provider = self._validate_push_route(route)
        return provider.send(
            content=self.content,
            user_context=self.user_context,
            routing_context={
                "project_id": str(self.project_id),
                "user_uuid": self._get_push_user_uuid(),
                "event_type_uuid": str(self.event_type_uuid),
                "app_id": self.app_id,
                "provider_id": route.provider_id,
            },
        )

    def send(self):
        try:
            if self.content.KIND == RenderedPushContent.KIND:
                self._send_push()
            else:
                self.provider.send(
                    content=self.content,
                    user_context=self.user_context,
                )
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
