#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License.

import uuid

import pytest

from exordos_notification.dm import models


@pytest.mark.parametrize(
    "statuses,expected",
    [
        (["ACTIVE", "ERROR"], "ERROR"),
        (["ACTIVE", "ACTIVE"], "ACTIVE"),
        (["ERROR", "IN_PROGRESS"], "IN_PROGRESS"),
    ],
)
def test_parent_status_aggregates_all_devices(database, statuses, expected):
    project_uuid = uuid.uuid4()
    provider = models.Provider(
        name="Test",
        project_id=project_uuid,
        protocol=models.SimpleSmtpProtocol(
            host="smtp.example.com",
            port=25,
            noreply_email_address="sender@example.com",
        ),
    )
    event_type = models.EventType(project_id=project_uuid)
    event = models.Event(
        project_id=project_uuid, event_type=event_type, event_params={}
    )
    with database.session_manager() as session:
        provider.insert(session=session)
        event_type.insert(session=session)
        event.insert(session=session)
        for status in statuses:
            models.RenderedEvent(
                provider=provider,
                content=models.RenderedEmailContent(),
                event_id=event.uuid,
                user_context={},
                status=status,
            ).insert(session=session)
        rows = session.execute(
            "SELECT system_status FROM incorrect_statuses WHERE uuid = %s",
            (str(event.uuid),),
        ).fetchall()
    assert [row["system_status"] for row in rows] == [expected]
