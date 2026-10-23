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

from restalchemy.api import controllers as ra_controllers
from restalchemy.api import resources

from exordos_notification.dm import models
from exordos_notification.user_api.api import versions


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


class EventController(ra_controllers.BaseResourceController):
    __resource__ = resources.ResourceByRAModel(models.Event, convert_underscore=False)
