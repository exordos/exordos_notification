#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License.

import unittest
import uuid
from unittest import mock

from restalchemy.common import exceptions

from exordos_notification.dm import models


class UserExchangeTest(unittest.TestCase):
    def test_parse_canonical_user_uuid(self):
        user_uuid = uuid.uuid4()
        exchange_type = models.Event.properties.properties[
            "exchange"
        ].get_property_type()
        exchange = exchange_type.from_simple_type(
            {"kind": "User", "user_uuid": str(user_uuid)}
        )
        iam = mock.Mock()

        exchange.get_context(iam)

        iam.get_user.assert_called_once_with(user_uuid)
        self.assertEqual(
            {"kind": "User", "user_uuid": str(user_uuid), "user_id": str(user_uuid)},
            exchange_type.to_simple_type(exchange),
        )

    def test_parse_legacy_user_id(self):
        user_uuid = uuid.uuid4()
        exchange_type = models.Event.properties.properties[
            "exchange"
        ].get_property_type()

        exchange = exchange_type.from_simple_type(
            {"kind": "User", "user_id": str(user_uuid)}
        )

        self.assertEqual(user_uuid, exchange.user_uuid)
        serialized = exchange_type.to_simple_type(exchange)
        self.assertEqual(str(user_uuid), serialized["user_id"])
        self.assertEqual(str(user_uuid), serialized["user_uuid"])
        self.assertEqual(
            user_uuid, exchange_type.from_simple_type(serialized).user_uuid
        )

    def test_reject_conflicting_user_identifiers(self):
        exchange_type = models.Event.properties.properties[
            "exchange"
        ].get_property_type()

        with self.assertRaises(exceptions.ParseError):
            exchange_type.from_simple_type(
                {
                    "kind": "User",
                    "user_id": str(uuid.uuid4()),
                    "user_uuid": str(uuid.uuid4()),
                }
            )


class EventRoutingTest(unittest.TestCase):
    def setUp(self):
        self.project_uuid = uuid.uuid4()
        self.user_uuid = uuid.uuid4()
        self.event_type = models.EventType(
            name="notification_message_create", project_id=self.project_uuid
        )
        self.default = self.template("Default")
        self.selected = self.template("Selected")
        self.event = models.Event(
            project_id=self.project_uuid,
            event_type=self.event_type,
            exchange=models.Event.properties.properties["exchange"]
            .get_property_type()
            .from_simple_type({"kind": "User", "user_id": str(self.user_uuid)}),
            event_params={},
        )
        self.iam = mock.Mock()
        self.iam.get_user.return_value = {"email": "recipient@example.com"}

    def template(self, title):
        provider = models.Provider(
            name=title,
            project_id=self.project_uuid,
            protocol=models.SimpleSmtpProtocol(
                host="smtp.example.com",
                port=25,
                noreply_email_address="sender@example.com",
            ),
        )
        return models.Template(
            name=title,
            project_id=self.project_uuid,
            params={},
            provider=provider,
            event_type=self.event_type,
            content=models.EmailContent(title=title, bodies=[]),
            is_default=title == "Default",
        )

    def test_user_binding_replaces_default_transport(self):
        binding = models.Binding(
            user=self.user_uuid,
            project_id=self.project_uuid,
            template=self.selected,
            event_type=self.event_type,
        )
        with (
            mock.patch.object(models.Template, "objects") as templates,
            mock.patch.object(models.Binding, "objects") as bindings,
        ):
            templates.get_all.return_value = [self.default]
            bindings.get_all.return_value = [binding]

            rendered = self.event.render(self.iam)

        self.assertEqual(["Selected"], [item.content.title for item in rendered])
        self.assertEqual(self.selected.provider, rendered[0].provider)

    def test_missing_binding_uses_default(self):
        with (
            mock.patch.object(models.Template, "objects") as templates,
            mock.patch.object(models.Binding, "objects") as bindings,
        ):
            templates.get_all.return_value = [self.default]
            bindings.get_all.return_value = []

            rendered = self.event.render(self.iam)

        self.assertEqual(["Default"], [item.content.title for item in rendered])

    def test_user_can_choose_multiple_transports(self):
        other = self.template("Other")
        selected = [self.selected, other]
        with (
            mock.patch.object(models.Template, "objects") as templates,
            mock.patch.object(models.Binding, "objects") as bindings,
        ):
            templates.get_all.return_value = [self.default]
            bindings.get_all.return_value = [
                models.Binding(
                    user=self.user_uuid,
                    project_id=self.project_uuid,
                    template=template,
                    event_type=self.event_type,
                )
                for template in selected
            ]

            rendered = self.event.render(self.iam)

        self.assertEqual(["Selected", "Other"], [e.content.title for e in rendered])
