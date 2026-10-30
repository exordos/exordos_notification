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

import uuid as sys_uuid

from gcl_iam import exceptions as iam_exceptions
from gcl_iam.api import controllers as iam_controllers
from restalchemy.api import controllers as ra_controllers
from restalchemy.api import resources
from restalchemy.dm import filters as dm_filters

from exordos_notification.dm import models
from exordos_notification.user_api.api import versions


class RootController(ra_controllers.Controller):
    """Controller for / endpoint"""

    def filter(self, filters):
        return (versions.API_VERSION_1_0,)


class ApiEndpointController(ra_controllers.RoutesListController):
    """Controller for /v1/ endpoint"""

    __TARGET_PATH__ = "/v1/"


class ManagedResourceController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "exordos_notification"


class ProviderController(ManagedResourceController):
    __policy_name__ = "provider"
    __resource__ = resources.ResourceByRAModel(
        models.Provider, convert_underscore=False
    )


class CatalogController(ManagedResourceController):
    def _enforce_and_override_project_id_in_kwargs(self, method, kwargs):
        if method == "read" and not self._enforcer.enforce_raw(
            f"exordos_notification.{self.__policy_name__}.read"
        ):
            if self._ctx_project_id is None:
                raise iam_exceptions.Forbidden()
            # Published catalog entries are available across project boundaries.
            kwargs[self.published_field] = dm_filters.EQ(self.published_value)
            return
        return super()._enforce_and_override_project_id_in_kwargs(method, kwargs)


class TemplateController(CatalogController):
    __policy_name__ = "template"
    published_field = "is_default"
    published_value = True
    __resource__ = resources.ResourceByRAModel(
        models.Template, convert_underscore=False
    )


class EventTypeController(CatalogController):
    __policy_name__ = "event_type"
    published_field = "status"
    published_value = "ACTIVE"
    __resource__ = resources.ResourceByRAModel(
        models.EventType, convert_underscore=False
    )


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
        self.get_context().iam_context.enforcer.enforce_raw(
            f"exordos_notification.event_type_{event.event_type.uuid}.{action}",
            do_raise=True,
        )

    def create(self, **kwargs):
        event = self.model(**kwargs)
        self._authorize(event, "create")
        event.insert()
        return event

    def get(self, uuid, **kwargs):
        event = super().get(uuid, **kwargs)
        self._authorize(event, "read")
        return event

    def filter(self, filters, order_by=None):
        self.get_context().iam_context.enforcer.enforce_raw(
            "exordos_notification.event.read", do_raise=True
        )
        return super().filter(filters, order_by=order_by)

    def update(self, uuid, **kwargs):
        self._authorize(self.get(uuid), "update")
        return super().update(uuid, **kwargs)

    def delete(self, uuid):
        event = self.get(uuid)
        self._authorize(event, "delete")
        return event.delete()
