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

import types
import unittest
import uuid
from unittest import mock

from restalchemy.api import controllers as ra_controllers
from restalchemy.storage.sql import orm

from genesis_notification.common import constants as c
from genesis_notification.common import exceptions as notification_exc
from genesis_notification.common.api.middlewares import errors as errors_mw
from genesis_notification.dm import models
from genesis_notification.user_api.api import controllers


PROJECT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
USER_UUID = uuid.UUID("22222222-2222-2222-2222-222222222222")


class OAuthClientScopeTest(unittest.TestCase):
    def _controller(self, token_claims, audience_name="audience-app"):
        token_info = types.SimpleNamespace(
            _token_info=token_claims,
            audience_name=audience_name,
            user_uuid=USER_UUID,
        )
        iam_engine = types.SimpleNamespace(
            token_info=token_info,
            get_introspection_info=lambda: types.SimpleNamespace(
                project_id=PROJECT_ID,
            ),
        )
        request = types.SimpleNamespace(iam_engine=iam_engine)
        return controllers.UserDeviceController(request)

    def test_apply_oauth_client_scope_overwrites_client_values(self):
        controller = self._controller({"client_id": "oauth-client"})

        resource = controller._apply_oauth_client_scope(
            {
                "project_id": "client-project",
                "user_uuid": "client-user",
                "app_id": "client-app",
            }
        )

        self.assertEqual(str(PROJECT_ID), resource["project_id"])
        self.assertEqual(str(USER_UUID), resource["user_uuid"])
        self.assertEqual("oauth-client", resource["app_id"])

    def test_app_id_falls_back_to_audience(self):
        controller = self._controller({}, audience_name="audience-app")

        self.assertEqual("audience-app", controller._get_app_id())


class NotificationErrorFormatTest(unittest.TestCase):
    def test_provider_not_allowed_error_response(self):
        response_class = mock.Mock(return_value="response")
        request = types.SimpleNamespace(ResponseClass=response_class)
        middleware = errors_mw.ErrorsHandlerMiddleware(application=None)

        result = middleware._construct_error_response(
            request,
            notification_exc.ProviderNotAllowedForApp(),
        )

        self.assertEqual("response", result)
        response_class.assert_called_once_with(
            status=422,
            json={
                "error": "provider_not_allowed_for_app",
                "error_description": (
                    "Provider is not allowed for this application."
                ),
            },
        )


class UserDeviceApiIdentifierTest(unittest.TestCase):
    def _controller(self):
        token_info = types.SimpleNamespace(
            _token_info={"client_id": "oauth-client"},
            audience_name="audience-app",
            user_uuid=USER_UUID,
        )
        iam_engine = types.SimpleNamespace(
            token_info=token_info,
            get_introspection_info=lambda: types.SimpleNamespace(
                project_id=PROJECT_ID,
            ),
        )
        request = types.SimpleNamespace(iam_engine=iam_engine)
        return controllers.UserDeviceController(request)

    def test_get_uses_fcm_token_as_api_identifier(self):
        controller = self._controller()

        with mock.patch.object(orm.ObjectCollection, "get_one") as get_one:
            controller.get("fcm-token")

        get_one.assert_called_once_with(
            filters={
                "fcm_token": "fcm-token",
                "project_id": str(PROJECT_ID),
                "user_uuid": str(USER_UUID),
                "app_id": "oauth-client",
            }
        )

    def test_delete_deactivates_device_by_fcm_token(self):
        controller = self._controller()
        device = types.SimpleNamespace(status=None, save=mock.Mock())

        with mock.patch.object(
            controller,
            "_get_by_fcm_token",
            return_value=device,
        ) as get_by_fcm_token:
            controller.delete("fcm-token")

        get_by_fcm_token.assert_called_once_with(fcm_token="fcm-token")
        self.assertEqual(c.AlwaysActiveStatus.INACTIVE.value, device.status)
        device.save.assert_called_once_with()

    def test_update_keeps_path_fcm_token_authoritative(self):
        controller = self._controller()
        device = types.SimpleNamespace(
            user_uuid=str(USER_UUID),
            app_id="oauth-client",
            provider_id="33333333-3333-3333-3333-333333333333",
            fcm_token="old-token",
            platform="ios",
            app_version="1.0",
            os_version="17",
            device_model="phone",
        )

        with (
            mock.patch.object(
                controller,
                "_get_by_fcm_token",
                return_value=device,
            ),
            mock.patch.object(
                controller,
                "_ensure_active_app_provider_mapping",
            ),
            mock.patch.object(
                controller,
                "_update_existing",
                return_value=device,
            ) as update_existing,
        ):
            controller.update(
                "path-token",
                fcm_token="body-token",
                platform="android",
            )

        update_existing.assert_called_once()
        resource = update_existing.call_args.args[1]
        self.assertEqual("path-token", resource["fcm_token"])
        self.assertEqual("android", resource["platform"])

    def test_provider_validation_uses_domain_error(self):
        controller = self._controller()

        with mock.patch.object(
            controller,
            "_get_active_provider",
            return_value=None,
        ):
            with self.assertRaises(notification_exc.ProviderNotAllowedForApp):
                controller._ensure_active_app_provider_mapping(
                    project_id=str(PROJECT_ID),
                    app_id="oauth-client",
                    provider_id=uuid.uuid4(),
                )


class UserNotificationTargetApiTest(unittest.TestCase):
    def _request(self):
        token_info = types.SimpleNamespace(
            _token_info={"client_id": "oauth-client"},
            audience_name="audience-app",
            user_uuid=USER_UUID,
        )
        iam_engine = types.SimpleNamespace(
            token_info=token_info,
            get_introspection_info=lambda: types.SimpleNamespace(
                project_id=PROJECT_ID,
            ),
        )
        return types.SimpleNamespace(iam_engine=iam_engine)

    def test_available_targets_are_scoped_to_current_app(self):
        controller = controllers.AvailableNotificationTargetController(
            self._request()
        )
        event_type_uuid = uuid.uuid4()
        provider_id = uuid.uuid4()
        target = types.SimpleNamespace(
            project_id=str(PROJECT_ID),
            app_id="oauth-client",
            provider_id=provider_id,
        )
        filtered_target = types.SimpleNamespace(
            project_id=str(PROJECT_ID),
            app_id="oauth-client",
            provider_id=uuid.uuid4(),
        )
        provider = types.SimpleNamespace(
            protocol=types.SimpleNamespace(KIND=models.FCMProtocol.KIND)
        )

        with (
            mock.patch.object(
                orm.ObjectCollection,
                "get_all",
                return_value=[target, filtered_target],
            ) as get_all,
            mock.patch.object(
                controller,
                "_get_active_provider",
                side_effect=[provider, provider],
            ),
            mock.patch.object(
                controller,
                "_get_active_app_provider_mapping",
                side_effect=[types.SimpleNamespace(), None],
            ),
        ):
            result = controller.filter(
                filters={"event_type_uuid": event_type_uuid}
            )

        self.assertEqual([target], result)
        filters = get_all.call_args.kwargs["filters"]
        self.assertEqual(str(PROJECT_ID), filters["project_id"])
        self.assertEqual(event_type_uuid, filters["event_type_uuid"])
        self.assertEqual("oauth-client", filters["app_id"])
        self.assertEqual(c.AlwaysActiveStatus.ACTIVE.value, filters["status"])

    def test_route_put_upserts_by_event_type_uuid(self):
        controller = controllers.UserNotificationRouteController(
            self._request()
        )
        event_type_uuid = uuid.uuid4()
        provider_id = uuid.uuid4()
        created_route = object()

        with (
            mock.patch.object(
                orm.ObjectCollection,
                "get_one_or_none",
                return_value=None,
            ),
            mock.patch.object(
                controller,
                "_ensure_active_app_provider_mapping",
            ) as ensure_mapping,
            mock.patch.object(
                controller,
                "_ensure_event_type_target_allowed",
            ) as ensure_target,
            mock.patch.object(
                ra_controllers.BaseResourceController,
                "create",
                return_value=created_route,
            ) as base_create,
        ):
            result = controller.update(
                uuid=event_type_uuid,
                provider_id=provider_id,
            )

        self.assertIs(created_route, result)
        ensure_mapping.assert_called_once_with(
            project_id=str(PROJECT_ID),
            app_id="oauth-client",
            provider_id=provider_id,
        )
        ensure_target.assert_called_once_with(
            project_id=str(PROJECT_ID),
            app_id="oauth-client",
            provider_id=provider_id,
            event_type_uuid=event_type_uuid,
        )
        base_create.assert_called_once_with(
            project_id=str(PROJECT_ID),
            user_uuid=str(USER_UUID),
            app_id="oauth-client",
            event_type_uuid=event_type_uuid,
            provider_id=provider_id,
        )


class SendingRoutingTest(unittest.TestCase):
    def test_fcm_selects_active_devices_by_route_context(self):
        protocol = models.FCMProtocol(
            project_id=str(PROJECT_ID),
            service_account_json="{}",
        )
        batch_result = types.SimpleNamespace(
            permanent_failures=lambda: [],
            total_failure=lambda: False,
        )
        provider_id = "33333333-3333-3333-3333-333333333333"

        with (
            mock.patch.object(orm.ObjectCollection, "get_all") as get_all,
            mock.patch.object(
                protocol,
                "_send_batch",
                return_value=batch_result,
            ),
        ):
            protocol.send(
                content=models.RenderedPushContent(),
                user_context={"user": {"uuid": str(USER_UUID)}},
                routing_context={
                    "project_id": str(PROJECT_ID),
                    "user_uuid": str(USER_UUID),
                    "app_id": "oauth-client",
                    "provider_id": provider_id,
                },
            )

        filters = get_all.call_args.kwargs["filters"]
        self.assertEqual(str(PROJECT_ID), filters["project_id"].value)
        self.assertEqual(str(USER_UUID), filters["user_uuid"].value)
        self.assertEqual("oauth-client", filters["app_id"].value)
        self.assertEqual(provider_id, filters["provider_id"].value)
        self.assertEqual(c.AlwaysActiveStatus.ACTIVE.value, filters["status"].value)

    def test_rendered_push_resolves_route_provider_before_send(self):
        provider_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
        route = types.SimpleNamespace(provider_id=provider_id)
        mapping = types.SimpleNamespace(uuid=uuid.uuid4())
        target = types.SimpleNamespace(uuid=uuid.uuid4())
        provider = models.Provider(
            uuid=provider_id,
            name="fcm",
            description="fcm",
            project_id=uuid.uuid4(),
            protocol=models.FCMProtocol(
                project_id=str(PROJECT_ID),
                service_account_json="{}",
            ),
        )
        rendered_event = models.RenderedEvent(
            content=models.RenderedPushContent(),
            event_id=uuid.uuid4(),
            project_id=PROJECT_ID,
            app_id="oauth-client",
            event_type_uuid=uuid.uuid4(),
            provider=provider,
            user_context={"user": {"uuid": str(USER_UUID)}},
        )

        def get_one_or_none(*args, **kwargs):
            filters = kwargs["filters"]
            if "event_type_uuid" in filters and "user_uuid" in filters:
                return route
            if "uuid" in filters:
                return provider
            if "event_type_uuid" in filters and "provider_id" in filters:
                return target
            if "app_id" in filters and "provider_id" in filters:
                return mapping
            return None

        with (
            mock.patch.object(
                orm.ObjectCollection,
                "get_one_or_none",
                side_effect=get_one_or_none,
            ),
            mock.patch.object(
                models.Provider,
                "send",
            ) as provider_send,
        ):
            rendered_event._send_push()

        provider_send.assert_called_once()
        routing_context = provider_send.call_args.kwargs["routing_context"]
        self.assertEqual(str(PROJECT_ID), routing_context["project_id"])
        self.assertEqual(str(USER_UUID), routing_context["user_uuid"])
        self.assertEqual("oauth-client", routing_context["app_id"])
        self.assertEqual(provider_id, routing_context["provider_id"])


if __name__ == "__main__":
    unittest.main()
