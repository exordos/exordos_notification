#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License.

import types
import unittest
import uuid
from unittest import mock

from restalchemy.common import exceptions

from exordos_notification.dm import models
from exordos_notification.user_api.api import controllers


class BindingTest(unittest.TestCase):
    def setUp(self):
        self.project_uuid = uuid.uuid4()
        self.user_uuid = uuid.uuid4()
        self.event_type = models.EventType(project_id=self.project_uuid)
        self.provider = models.Provider(
            name="Email",
            project_id=self.project_uuid,
            protocol=models.SimpleSmtpProtocol(
                host="smtp.example.com",
                port=25,
                noreply_email_address="sender@example.com",
            ),
        )
        self.template = models.Template(
            name="Email",
            project_id=self.project_uuid,
            params={},
            provider=self.provider,
            event_type=self.event_type,
            content=models.EmailContent(),
        )

    def binding(self, **kwargs):
        values = {
            "user": self.user_uuid,
            "project_id": self.project_uuid,
            "event_type": self.event_type,
            "template": self.template,
        }
        values.update(kwargs)
        return models.Binding(**values)

    def test_reject_template_from_another_project(self):
        with self.assertRaises(exceptions.ValidationErrorException):
            self.binding(project_id=uuid.uuid4()).validate()

    def test_reject_template_for_another_event_type(self):
        other = models.EventType(project_id=self.project_uuid)
        with self.assertRaises(exceptions.ValidationErrorException):
            self.binding(event_type=other).validate()

    def test_create_uses_authenticated_owner(self):
        introspection = types.SimpleNamespace(
            project_id=self.project_uuid,
            user_info=types.SimpleNamespace(uuid=str(self.user_uuid)),
        )
        request = mock.Mock()
        request.context.iam_context.get_introspection_info.return_value = introspection
        controller = controllers.BindingController(request)

        with mock.patch.object(models.Binding, "insert"):
            binding = controller.create(
                project_id=uuid.uuid4(),
                user=uuid.uuid4(),
                event_type=self.event_type,
                template=self.template,
            )

        self.assertEqual(self.user_uuid, binding.user)
        self.assertEqual(self.project_uuid, binding.project_id)
