# Licensed under the Apache License, Version 2.0 (the "License").

import datetime
import os
import uuid

from exordos_notification.cmd import metrics
from exordos_notification.dm import models


def queue_count(connection_url, stage, channel, state):
    prefix = f'exordos_notification_queue_items{{stage="{stage}",channel="{channel}",state="{state}"}} '
    samples = metrics.queue_metrics(connection_url)
    assert "exordos_notification_queue_collection_up 1" in samples
    return int(next(s.removeprefix(prefix) for s in samples if s.startswith(prefix)))


def test_queue_snapshot_separates_due_retries_and_terminal_rows(database):
    connection_url = (
        os.environ.get("NOTIFICATION_TEST_DB_URL") or os.environ["DATABASE_URI"]
    )
    project = uuid.uuid4()
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    future = now + datetime.timedelta(hours=1)
    past = now - datetime.timedelta(minutes=10)
    provider = models.Provider(
        name="Monitoring test",
        project_id=project,
        protocol=models.FirebaseProtocol(),
    )
    event_type = models.EventType(project_id=project, name="Monitoring test")
    event = models.Event(
        project_id=project,
        event_type=event_type,
        event_params={},
        next_retry_at=past,
        last_retry_at=future,
    )
    states = {
        "ready": (past, future, "IN_PROGRESS"),
        "scheduled": (now + datetime.timedelta(minutes=15), future, "ERROR"),
        "expired": (past, now - datetime.timedelta(minutes=5), "ERROR"),
        "no_retry": (future + datetime.timedelta(hours=1), future, "ERROR"),
        "accepted": (past, future, "ACTIVE"),
    }
    deliveries = [
        models.RenderedEvent(
            event_id=uuid.uuid4(),
            provider=provider,
            content=models.RenderedFirebaseAndroidContent(
                device_uuid=uuid.uuid4(),
                registration_token="synthetic-token",
                data={"payload": "synthetic-payload"},
            ),
            user_context={},
            next_retry_at=next_retry,
            last_retry_at=last_retry,
            status=status,
        )
        for next_retry, last_retry, status in states.values()
    ]
    series = [("builder", "all", "ready")]
    series.extend(("delivery", "fcm", state) for state in states)
    before = [queue_count(connection_url, *labels) for labels in series]
    with database.session_manager() as session:
        for model in (provider, event_type, event, *deliveries):
            model.insert(session=session)
    try:
        assert [queue_count(connection_url, *labels) for labels in series] == [
            count + 1 for count in before
        ]
    finally:
        with database.session_manager() as session:
            for model in reversed((provider, event_type, event, *deliveries)):
                model.delete(session=session)
