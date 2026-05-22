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

from restalchemy.api import routes

from genesis_notification.user_api.api import controllers


class ProviderRoute(routes.Route):
    __controller__ = controllers.ProviderController


class TemplateRoute(routes.Route):
    __controller__ = controllers.TemplateController


class EventTypeRoute(routes.Route):
    __controller__ = controllers.EventTypeController


class EventRoute(routes.Route):
    __controller__ = controllers.EventController


class AppProviderMappingRoute(routes.Route):
    __controller__ = controllers.AppProviderMappingController


class EventTypeNotificationTargetRoute(routes.Route):
    __controller__ = controllers.EventTypeNotificationTargetController


class AvailableNotificationTargetRoute(routes.Route):
    __controller__ = controllers.AvailableNotificationTargetController
    __allow_methods__ = [routes.FILTER]


class UserDeviceRoute(routes.Route):
    __controller__ = controllers.UserDeviceController

    def get_method_by_route_type(self, route_type):
        if route_type != routes.COLLECTION_ROUTE and self._req.method == "PATCH":
            return routes.UPDATE
        return super().get_method_by_route_type(route_type)

    def build_openapi_specification(self, current_path="/", parameters=None):
        paths_result, schemas_result = super().build_openapi_specification(
            current_path=current_path,
            parameters=parameters,
        )

        old_parameter = "{UserDeviceUuid}"
        new_parameter = "{fcm_token}"

        for path in list(paths_result.keys()):
            if old_parameter not in path:
                continue

            path_spec = paths_result.pop(path)
            new_path = path.replace(old_parameter, new_parameter)
            for method_spec in path_spec.values():
                for parameter in method_spec.get("parameters", []):
                    if parameter.get("name") == "UserDeviceUuid":
                        parameter["name"] = "fcm_token"

            if "put" in path_spec and "patch" not in path_spec:
                path_spec["patch"] = path_spec["put"].copy()
                path_spec["patch"]["operationId"] = path_spec["patch"][
                    "operationId"
                ].replace("Put", "Patch", 1)

            paths_result[new_path] = path_spec

        return paths_result, schemas_result


class UserNotificationRouteApiRoute(routes.Route):
    __controller__ = controllers.UserNotificationRouteController

    def build_openapi_specification(self, current_path="/", parameters=None):
        paths_result, schemas_result = super().build_openapi_specification(
            current_path=current_path,
            parameters=parameters,
        )

        old_parameter = "{UserNotificationRouteUuid}"
        new_parameter = "{event_type_uuid}"

        for path in list(paths_result.keys()):
            if old_parameter not in path:
                continue

            path_spec = paths_result.pop(path)
            new_path = path.replace(old_parameter, new_parameter)
            for method_spec in path_spec.values():
                for parameter in method_spec.get("parameters", []):
                    if parameter.get("name") == "UserNotificationRouteUuid":
                        parameter["name"] = "event_type_uuid"
            paths_result[new_path] = path_spec

        return paths_result, schemas_result


class ApiEndpointRoute(routes.Route):
    """Handler for /v1.0/ endpoint"""

    __controller__ = controllers.ApiEndpointController
    __allow_methods__ = [routes.FILTER]

    providers = routes.route(ProviderRoute)
    templates = routes.route(TemplateRoute)
    event_types = routes.route(EventTypeRoute)
    events = routes.route(EventRoute)
    app_provider_mappings = routes.route(AppProviderMappingRoute)
    event_type_notification_targets = routes.route(EventTypeNotificationTargetRoute)
    available_notification_targets = routes.route(AvailableNotificationTargetRoute)
    user_devices = routes.route(UserDeviceRoute)
    user_notification_routes = routes.route(UserNotificationRouteApiRoute)
