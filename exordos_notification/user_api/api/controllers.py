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
import uuid as sys_uuid

from gcl_iam import exceptions as iam_exceptions
from restalchemy.api import controllers as ra_controllers
from restalchemy.api import resources
from restalchemy.dm import filters as dm_filters

from exordos_notification.common import push
from exordos_notification.dm import models
from exordos_notification.user_api.api import versions


class RootController(ra_controllers.Controller):
    """Controller for / endpoint"""

    def filter(self, filters):
        return (versions.API_VERSION_1_0,)


class ApiEndpointController(ra_controllers.RoutesListController):
    """Controller for /v1/ endpoint"""

    __TARGET_PATH__ = "/v1/"


class ManagedResourceController(ra_controllers.BaseResourceControllerPaginated):
    def _authorize(self, action):
        self.get_context().iam_context.enforcer.enforce_raw(
            f"exordos_notification.{self.policy_resource}.{action}",
            do_raise=True,
        )

    def get_autofilters(self):
        self._authorize("read")
        return {}

    def create(self, **kwargs):
        self._authorize("create")
        return super().create(**kwargs)

    def update(self, uuid, **kwargs):
        self._authorize("update")
        return super().update(uuid, **kwargs)

    def delete(self, uuid):
        self._authorize("delete")
        return super().delete(uuid)


class ProviderController(ManagedResourceController):
    policy_resource = "provider"
    __resource__ = resources.ResourceByRAModel(
        models.Provider, convert_underscore=False
    )


class TemplateController(ManagedResourceController):
    policy_resource = "template"
    __resource__ = resources.ResourceByRAModel(
        models.Template, convert_underscore=False
    )

    def get_autofilters(self):
        iam = self.get_context().iam_context
        if iam.enforcer.enforce_raw("exordos_notification.template.read"):
            return {}
        if iam.get_introspection_info().project_id is None:
            raise iam_exceptions.Forbidden()
        # Published templates form the transport catalog for ordinary users.
        return {"is_default": dm_filters.EQ(True)}


class EventTypeController(ManagedResourceController):
    policy_resource = "event_type"
    __resource__ = resources.ResourceByRAModel(
        models.EventType, convert_underscore=False
    )

    def get_autofilters(self):
        # RA resolves relationship URIs through this controller during POST.
        # Preserve that internal lookup for the existing public email ingress.
        if self.request.method == "POST" and self.request.path == "/v1/events/":
            body = self.request.json_body
            if not push.is_push_event(body.get("event_type"), body.get("event_params")):
                return {}
        iam = self.get_context().iam_context
        if iam.enforcer.enforce_raw("exordos_notification.event_type.read"):
            return {}
        if iam.get_introspection_info().project_id is None:
            raise iam_exceptions.Forbidden()
        return {"status": dm_filters.EQ("ACTIVE")}


class BindingController(ra_controllers.BaseResourceController):
    __resource__ = resources.ResourceByRAModel(models.Binding, convert_underscore=False)

    def get_autovalues(self):
        info = self.get_context().iam_context.get_introspection_info()
        if info.project_id is None:
            raise iam_exceptions.Forbidden()
        return {
            "project_id": info.project_id,
            "user": sys_uuid.UUID(str(info.user_info.uuid)),
        }

    def get_autofilters(self):
        return {
            name: dm_filters.EQ(value) for name, value in self.get_autovalues().items()
        }


class EventController(ra_controllers.BaseResourceController):
    __resource__ = resources.ResourceByRAModel(models.Event, convert_underscore=False)

    def _authorize(self, event, action):
        organization = sys_uuid.UUID(event.event_params["organization_uuid"])
        self.get_context().iam_context.enforcer.enforce_raw(
            f"exordos_notification.source_{organization}.{action}",
            do_raise=True,
        )

    def create(self, **kwargs):
        mobile = push.is_push_event(kwargs["event_type"], kwargs["event_params"])
        if self.request.headers.get("X-Notification-Relay") == "1" and not mobile:
            raise iam_exceptions.Forbidden()
        if mobile:
            for field in (
                "created_at",
                "updated_at",
                "status",
                "status_description",
                "errors",
                "next_retry_at",
                "retry_count",
            ):
                kwargs.pop(field, None)
            deadline = datetime.datetime.now(
                datetime.timezone.utc
            ) + datetime.timedelta(hours=1)
            kwargs["last_retry_at"] = min(
                kwargs.get("last_retry_at", deadline), deadline
            )
        event = self.model(**kwargs)
        if mobile:
            self._authorize(event, "create")
        event.insert()
        return event

    def get(self, uuid, **kwargs):
        event = super().get(uuid, **kwargs)
        mobile = push.is_push_event(event.event_type, event.event_params)
        if self.request.headers.get("X-Notification-Relay") == "1" and not mobile:
            raise iam_exceptions.Forbidden()
        if mobile:
            self._authorize(event, "read")
        return event

    def filter(self, filters, order_by=None):
        self.get_context().iam_context.enforcer.enforce_raw(
            "exordos_notification.event.read",
            do_raise=True,
        )
        return super().filter(filters, order_by=order_by)

    def update(self, uuid, **kwargs):
        event = self.get(uuid)
        if push.is_push_event(
            event.event_type, event.event_params
        ) or push.is_push_event(
            kwargs.get("event_type", event.event_type),
            kwargs.get("event_params", event.event_params),
        ):
            raise iam_exceptions.Forbidden()
        return super().update(uuid, **kwargs)

    def delete(self, uuid):
        event = self.get(uuid)
        if push.is_push_event(event.event_type, event.event_params):
            raise iam_exceptions.Forbidden()
        return event.delete()
