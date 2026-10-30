# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").

import datetime
import json
import pathlib
from unittest import mock

import pytest

from exordos_notification.common import constants, transports
from exordos_notification.dm import models
from exordos_notification.tests.unit import test_push_transports


@pytest.mark.parametrize("priority", ["normal", "high"])
def test_topic_update_is_opaque_and_preserves_delivery_priority(priority):
    event, template = test_push_transports.push_event(
        constants.NOTIFICATION_TOPIC_UPDATE
    )
    event.event_params = dict(
        event.event_params, kind=constants.NOTIFICATION_TOPIC_UPDATE, priority=priority
    )
    event.validate()
    with (
        mock.patch.object(models.Template, "objects") as templates,
        mock.patch.object(models.Binding, "objects") as bindings,
    ):
        templates.get_all.return_value = [template]
        bindings.get_all.return_value = []
        iam = mock.Mock()
        rendered = event.render(iam)
    iam.get_user.assert_not_called()
    assert len(rendered) == 2
    for row in rendered:
        assert row.content.priority == priority
        assert row.content.payload["kind"] == constants.NOTIFICATION_TOPIC_UPDATE
        assert row.content.payload["content"]["kind"] == "encrypted"
        assert "registration_token" not in row.content.payload
        session = mock.Mock()
        session.post.return_value.status_code = 200
        with mock.patch.object(transports, "firebase_session", return_value=session):
            template.provider.protocol.send(row.content, {})
        request = session.post.call_args.kwargs["json"]["message"]
        assert request["android"]["priority"] == priority
        assert "notification" not in request


def test_python_topic_fixture_reaches_android_without_enrichment():
    fixture = json.loads(
        (
            pathlib.Path(__file__).parents[1] / "fixtures/topic_push_events.json"
        ).read_text()
    )
    rendered = models.MobilePushContent().render(
        {
            "_notification_event": fixture["event"],
            "_notification_expires_at": datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=1),
        }
    )
    assert len(rendered) == 1
    assert rendered[0].payload == fixture["payloads"]["first"]
    assert rendered[0].priority == "high"
    assert "registration_token" not in json.dumps(rendered[0].payload)
