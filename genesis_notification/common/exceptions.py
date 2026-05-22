#    Copyright 2026 Genesis Corporation.
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


class NotificationAPIError(Exception):
    status_code = 400
    error_code = "notification_error"
    message = "Notification API error."

    def __init__(self, message=None):
        self.message = message or self.message
        super().__init__(self.message)


class UserNotAllowedForOAuthClient(NotificationAPIError):
    status_code = 403
    error_code = "user_not_allowed_for_oauth_client"
    message = "User is not allowed to use this OAuth client."


class ProviderNotAllowedForApp(NotificationAPIError):
    status_code = 422
    error_code = "provider_not_allowed_for_app"
    message = "Provider is not allowed for this application."


class ProviderNotAllowedForEventType(NotificationAPIError):
    status_code = 422
    error_code = "provider_not_allowed_for_event_type"
    message = "Provider is not allowed for this event type."


class EventTypeRequired(NotificationAPIError):
    status_code = 422
    error_code = "event_type_required"
    message = "event_type_uuid is required."
