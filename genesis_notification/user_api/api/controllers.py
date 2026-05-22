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
from gcl_iam import exceptions as iam_exc
from restalchemy.api import controllers as ra_controllers
from restalchemy.api import constants as ra_constants
from restalchemy.api import packers
from restalchemy.api import resources

from genesis_notification.common import constants as c
from genesis_notification.common import exceptions as notification_exc
from genesis_notification.dm import models
from genesis_notification.user_api.api import versions


class UserDeviceResource(resources.ResourceByRAModel):
    def get_resource_id(self, model):
        return model.fcm_token

    def get_id_type(self):
        return models.UserDevice.properties.properties[
            "fcm_token"
        ].get_property_type()


class UserNotificationRouteResource(resources.ResourceByRAModel):
    def get_resource_id(self, model):
        return str(model.event_type_uuid)

    def get_id_type(self):
        return models.UserNotificationRoute.properties.properties[
            "event_type_uuid"
        ].get_property_type()


class RootController(ra_controllers.Controller):
    """Controller for / endpoint"""

    def filter(self, filters):
        return (versions.API_VERSION_1_0,)


class ApiEndpointController(ra_controllers.RoutesListController):
    """Controller for /v1/ endpoint"""

    __TARGET_PATH__ = "/v1/"


class ProviderController(ra_controllers.BaseResourceController):
    __resource__ = resources.ResourceByRAModel(
        models.Provider, convert_underscore=False
    )


class TemplateController(ra_controllers.BaseResourceController):
    __resource__ = resources.ResourceByRAModel(
        models.Template, convert_underscore=False
    )


class EventTypeController(ra_controllers.BaseResourceController):
    __resource__ = resources.ResourceByRAModel(
        models.EventType, convert_underscore=False
    )


class ProviderRoutingValidationMixin:
    def _get_active_provider(self, provider_id):
        return models.Provider.objects.get_one_or_none(
            filters={
                "uuid": provider_id,
                "status": c.AlwaysActiveStatus.ACTIVE.value,
            }
        )

    def _ensure_active_provider(self, provider_id):
        if not self._get_active_provider(provider_id):
            raise notification_exc.ProviderNotAllowedForApp()

    def _get_active_app_provider_mapping(self, project_id, app_id, provider_id):
        return models.AppProviderMapping.objects.get_one_or_none(
            filters={
                "project_id": project_id,
                "app_id": app_id,
                "provider_id": provider_id,
                "status": c.AlwaysActiveStatus.ACTIVE.value,
            }
        )

    def _ensure_active_app_provider_mapping(self, project_id, app_id, provider_id):
        self._ensure_active_provider(provider_id)

        if not self._get_active_app_provider_mapping(
            project_id=project_id,
            app_id=app_id,
            provider_id=provider_id,
        ):
            raise notification_exc.ProviderNotAllowedForApp()


class OAuthClientScopedControllerMixin(ProviderRoutingValidationMixin):
    def _get_token_info(self):
        return self.request.iam_engine.token_info

    def _get_introspection_info(self):
        return self.request.iam_engine.get_introspection_info()

    def _get_project_id(self):
        project_id = self._get_introspection_info().project_id
        if project_id is None:
            raise iam_exc.Forbidden()
        return str(project_id)

    def _get_user_uuid(self):
        return str(self._get_token_info().user_uuid)

    def _get_app_id(self):
        token_info = self._get_token_info()
        raw_token_info = getattr(token_info, "_token_info", {})

        app_id = (
            raw_token_info.get("client_id")
            or raw_token_info.get("azp")
            or token_info.audience_name
        )
        if isinstance(app_id, (list, tuple)):
            app_id = app_id[0] if app_id else None

        if not app_id:
            raise notification_exc.UserNotAllowedForOAuthClient()

        return app_id

    def _apply_oauth_client_scope(self, resource):
        resource["project_id"] = self._get_project_id()
        resource["user_uuid"] = self._get_user_uuid()
        resource["app_id"] = self._get_app_id()
        return resource

    def _get_oauth_client_scope(self):
        return {
            "project_id": self._get_project_id(),
            "user_uuid": self._get_user_uuid(),
            "app_id": self._get_app_id(),
        }


class EventController(
    OAuthClientScopedControllerMixin,
    ra_controllers.BaseResourceController,
):
    __resource__ = resources.ResourceByRAModel(
        models.Event,
        hidden_fields=["project_id", "app_id"],
        convert_underscore=False,
    )

    def create(self, **kwargs):
        kwargs["project_id"] = self._get_project_id()
        kwargs["app_id"] = self._get_app_id()
        return super().create(**kwargs)


class UserScopedResourceControllerMixin(OAuthClientScopedControllerMixin):
    def _apply_scope_filters(self, filters):
        scoped_filters = filters.copy()
        scoped_filters.update(self._get_oauth_client_scope())
        return scoped_filters

    def get(self, uuid, **kwargs):
        filters = self._apply_scope_filters({"uuid": uuid})
        return self.model.objects.get_one(filters=filters)

    def filter(self, filters, order_by=None):
        return super().filter(
            self._apply_scope_filters(filters),
            order_by=order_by,
        )

    def delete(self, uuid):
        self.get(uuid=uuid).delete()


class AppProviderMappingController(
    ProviderRoutingValidationMixin,
    ra_controllers.BaseResourceController,
):
    __resource__ = resources.ResourceByRAModel(
        models.AppProviderMapping,
        convert_underscore=False,
    )

    def create(self, **kwargs):
        self._ensure_active_provider(kwargs["provider_id"])
        return super().create(**kwargs)

    def update(self, uuid, **kwargs):
        if "provider_id" in kwargs:
            self._ensure_active_provider(kwargs["provider_id"])
        return super().update(uuid=uuid, **kwargs)


class EventTypeNotificationTargetController(
    ProviderRoutingValidationMixin,
    ra_controllers.BaseResourceController,
):
    __resource__ = resources.ResourceByRAModel(
        models.EventTypeNotificationTarget,
        convert_underscore=False,
    )

    def create(self, **kwargs):
        self._ensure_active_app_provider_mapping(
            project_id=kwargs["project_id"],
            app_id=kwargs["app_id"],
            provider_id=kwargs["provider_id"],
        )
        return super().create(**kwargs)

    def update(self, uuid, **kwargs):
        existing = self.get(uuid=uuid)

        project_id = kwargs.get("project_id", existing.project_id)
        app_id = kwargs.get("app_id", existing.app_id)
        provider_id = kwargs.get("provider_id", existing.provider_id)

        self._ensure_active_app_provider_mapping(
            project_id=project_id,
            app_id=app_id,
            provider_id=provider_id,
        )

        return super().update(uuid=uuid, **kwargs)


class AvailableNotificationTargetController(
    OAuthClientScopedControllerMixin,
    ra_controllers.BaseResourceController,
):
    __resource__ = EventTypeNotificationTargetController.__resource__

    def filter(self, filters, order_by=None):
        event_type_uuid = self._get_single_filter_value(filters, "event_type_uuid")
        if not event_type_uuid:
            raise notification_exc.EventTypeRequired()

        targets = models.EventTypeNotificationTarget.objects.get_all(
            filters={
                "project_id": self._get_project_id(),
                "event_type_uuid": event_type_uuid,
                "app_id": self._get_app_id(),
                "status": c.AlwaysActiveStatus.ACTIVE.value,
            },
            order_by=order_by,
        )

        return [
            target for target in targets
            if self._is_available_target(target)
        ]

    def _get_single_filter_value(self, filters, name):
        if name not in filters:
            return None
        filter_value = filters[name]
        return getattr(filter_value, "value", filter_value)

    def _is_available_target(self, target):
        provider = self._get_active_provider(target.provider_id)
        if not provider or provider.protocol.KIND != models.FCMProtocol.KIND:
            return False

        return bool(
            self._get_active_app_provider_mapping(
                project_id=target.project_id,
                app_id=target.app_id,
                provider_id=target.provider_id,
            )
        )


class UserDeviceController(
    UserScopedResourceControllerMixin,
    ra_controllers.BaseResourceController,
):
    __resource__ = UserDeviceResource(
        models.UserDevice,
        hidden_fields=["uuid", "project_id", "user_uuid", "app_id"],
        convert_underscore=False,
    )

    def _update_existing(self, existing, resource):
        existing.user_uuid = resource["user_uuid"]
        existing.app_id = resource["app_id"]
        existing.provider_id = resource["provider_id"]
        existing.fcm_token = resource["fcm_token"]
        existing.platform = resource["platform"]

        existing.app_version = resource.get("app_version", "")
        existing.os_version = resource.get("os_version", "")
        existing.device_model = resource.get("device_model", "")

        existing.status = c.AlwaysActiveStatus.ACTIVE.value

        existing.save()

        return existing

    def create(self, **kwargs):
        kwargs = self._apply_oauth_client_scope(kwargs)
        self._ensure_active_app_provider_mapping(
            project_id=kwargs["project_id"],
            app_id=kwargs["app_id"],
            provider_id=kwargs["provider_id"],
        )

        existing = models.UserDevice.objects.get_one_or_none(
            filters={
                "project_id": kwargs["project_id"],
                "fcm_token": kwargs["fcm_token"],
            }
        )

        if existing:
            return self._update_existing(existing, kwargs)

        return super().create(**kwargs)

    def get(self, uuid, **kwargs):
        return self._get_by_fcm_token(fcm_token=uuid)

    def update(self, uuid, **kwargs):
        existing = self._get_by_fcm_token(fcm_token=uuid)
        kwargs = self._apply_oauth_client_scope(kwargs)

        resource = {
            "user_uuid": kwargs["user_uuid"],
            "app_id": kwargs["app_id"],
            "provider_id": kwargs.get("provider_id", existing.provider_id),
            "fcm_token": uuid,
            "platform": kwargs.get("platform", existing.platform),
            "app_version": kwargs.get("app_version", existing.app_version),
            "os_version": kwargs.get("os_version", existing.os_version),
            "device_model": kwargs.get("device_model", existing.device_model),
        }

        self._ensure_active_app_provider_mapping(
            project_id=kwargs["project_id"],
            app_id=resource["app_id"],
            provider_id=resource["provider_id"],
        )

        return self._update_existing(existing, resource)

    def delete(self, uuid):
        existing = self._get_by_fcm_token(fcm_token=uuid)
        existing.status = c.AlwaysActiveStatus.INACTIVE.value
        existing.save()

    def _get_by_fcm_token(self, fcm_token):
        filters = self._apply_scope_filters({"fcm_token": fcm_token})
        return self.model.objects.get_one(filters=filters)

    def do_resource(self, uuid, parent_resource=None):
        if self.request.method != "PATCH":
            return super().do_resource(uuid, parent_resource=parent_resource)

        kwargs = self._make_kwargs(parent_resource)
        parsed_id = self._parse_resource_uuid(
            "fcm_token",
            uuid,
            self.get_resource().get_id_type(),
        )
        self.request.api_context.set_active_method(ra_constants.UPDATE)
        content_type = packers.get_content_type(self.request.headers)
        packer = self.get_packer(content_type)
        kwargs.update(packer.unpack(value=self.request.body))
        kwargs.pop("uuid", None)

        return self.process_result(
            result=self.update(uuid=parsed_id, **kwargs),
            add_location=ra_constants.UPDATE in self.__generate_location_for__,
        )


class UserNotificationRouteController(
    UserScopedResourceControllerMixin,
    ra_controllers.BaseResourceController,
):
    __resource__ = UserNotificationRouteResource(
        models.UserNotificationRoute,
        hidden_fields=["uuid", "project_id", "user_uuid", "app_id"],
        convert_underscore=False,
    )

    def _update_existing(self, existing, resource):
        existing.app_id = resource["app_id"]
        existing.provider_id = resource["provider_id"]
        existing.status = resource.get(
            "status",
            c.AlwaysActiveStatus.ACTIVE.value,
        )

        existing.save()

        return existing

    def create(self, **kwargs):
        kwargs = self._apply_oauth_client_scope(kwargs)
        self._ensure_active_app_provider_mapping(
            project_id=kwargs["project_id"],
            app_id=kwargs["app_id"],
            provider_id=kwargs["provider_id"],
        )
        self._ensure_event_type_target_allowed(
            project_id=kwargs["project_id"],
            app_id=kwargs["app_id"],
            provider_id=kwargs["provider_id"],
            event_type_uuid=kwargs["event_type_uuid"],
        )

        existing = models.UserNotificationRoute.objects.get_one_or_none(
            filters={
                "project_id": kwargs["project_id"],
                "user_uuid": kwargs["user_uuid"],
                "event_type_uuid": kwargs["event_type_uuid"],
            }
        )

        if existing:
            return self._update_existing(existing, kwargs)

        return super().create(**kwargs)

    def update(self, uuid, **kwargs):
        kwargs = self._apply_oauth_client_scope(kwargs)
        kwargs["event_type_uuid"] = uuid

        existing = models.UserNotificationRoute.objects.get_one_or_none(
            filters={
                "project_id": kwargs["project_id"],
                "user_uuid": kwargs["user_uuid"],
                "event_type_uuid": kwargs["event_type_uuid"],
            }
        )

        if not existing:
            self._ensure_active_app_provider_mapping(
                project_id=kwargs["project_id"],
                app_id=kwargs["app_id"],
                provider_id=kwargs["provider_id"],
            )
            self._ensure_event_type_target_allowed(
                project_id=kwargs["project_id"],
                app_id=kwargs["app_id"],
                provider_id=kwargs["provider_id"],
                event_type_uuid=kwargs["event_type_uuid"],
            )
            return super().create(**kwargs)

        resource = {
            "app_id": kwargs["app_id"],
            "provider_id": kwargs.get("provider_id", existing.provider_id),
            "event_type_uuid": kwargs.get(
                "event_type_uuid",
                existing.event_type_uuid,
            ),
            "status": kwargs.get("status", existing.status),
        }

        self._ensure_active_app_provider_mapping(
            project_id=kwargs["project_id"],
            app_id=resource["app_id"],
            provider_id=resource["provider_id"],
        )
        self._ensure_event_type_target_allowed(
            project_id=kwargs["project_id"],
            app_id=resource["app_id"],
            provider_id=resource["provider_id"],
            event_type_uuid=resource["event_type_uuid"],
        )

        existing.event_type_uuid = resource["event_type_uuid"]

        return self._update_existing(existing, resource)

    def get(self, uuid, **kwargs):
        filters = self._apply_scope_filters({"event_type_uuid": uuid})
        return self.model.objects.get_one(filters=filters)

    def delete(self, uuid):
        existing = self.get(uuid=uuid)
        existing.status = c.AlwaysActiveStatus.INACTIVE.value
        existing.save()

    def do_resource(self, uuid, parent_resource=None):
        kwargs = self._make_kwargs(parent_resource)
        parsed_id = self._parse_resource_uuid(
            "event_type_uuid",
            uuid,
            self.get_resource().get_id_type(),
        )
        api_context = self.request.api_context

        if self.request.method == "GET":
            api_context.set_active_method(ra_constants.GET)
            return self.process_result(result=self.get(uuid=parsed_id, **kwargs))

        if self.request.method == "PUT":
            api_context.set_active_method(ra_constants.UPDATE)
            content_type = packers.get_content_type(self.request.headers)
            packer = self.get_packer(content_type)
            kwargs.update(packer.unpack(value=self.request.body))
            kwargs.pop("uuid", None)
            kwargs.pop("event_type_uuid", None)
            return self.process_result(
                result=self.update(uuid=parsed_id, **kwargs),
                add_location=(
                    ra_constants.UPDATE in self.__generate_location_for__
                ),
            )

        if self.request.method == "DELETE":
            api_context.set_active_method(ra_constants.DELETE)
            result = self.delete(uuid=parsed_id)
            return self.process_result(
                result=result,
                status_code=200 if result else 204,
            )

        return super().do_resource(uuid, parent_resource=parent_resource)

    def _ensure_event_type_target_allowed(
        self,
        project_id,
        app_id,
        provider_id,
        event_type_uuid,
    ):
        target = models.EventTypeNotificationTarget.objects.get_one_or_none(
            filters={
                "project_id": project_id,
                "event_type_uuid": event_type_uuid,
                "app_id": app_id,
                "provider_id": provider_id,
                "status": c.AlwaysActiveStatus.ACTIVE.value,
            }
        )
        if not target:
            raise notification_exc.ProviderNotAllowedForEventType()
