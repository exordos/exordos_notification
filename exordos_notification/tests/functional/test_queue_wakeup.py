# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.

import contextlib
import datetime
import os
import queue
import threading
import time
from unittest import mock
import uuid

import psycopg
from psycopg import sql
import pytest
import requests
from restalchemy.dm import filters

from exordos_notification.dm import models
from exordos_notification.services.builders import agents as builders
from exordos_notification.services.providers import agents as providers


@pytest.fixture
def database_url(database):
    return os.environ.get("NOTIFICATION_TEST_DB_URL") or os.environ["DATABASE_URI"]


@pytest.fixture
def email_queue(database):
    project = uuid.uuid4()
    event_type = models.EventType(project_id=project)
    event_type.insert()
    provider = models.Provider(
        project_id=project,
        name="Queue wakeup",
        protocol=models.SimpleSmtpProtocol(
            host="smtp.example.com", port=25, noreply_email_address="sender@example.com"
        ),
    )
    provider.insert()
    return event_type, provider


def template(event_type, provider, title):
    models.Template(
        project_id=event_type.project_id,
        name=title,
        params={},
        event_type=event_type,
        provider=provider,
        content=models.EmailContent(title=title, recipient="recipient@example.com"),
        is_default=True,
    ).insert()


def listen(connection, channel):
    connection.execute(sql.SQL("LISTEN {}").format(sql.Identifier(channel)))


def notification(connection, timeout):
    with contextlib.closing(
        connection.notifies(timeout=timeout, stop_after=1)
    ) as signals:
        return next(signals, None)


@pytest.mark.parametrize("rendered", [False, True])
def test_enqueue_signals_only_on_commit(database, database_url, email_queue, rendered):
    event_type, provider = email_queue
    event = models.Event(
        project_id=event_type.project_id, event_type=event_type, event_params={}
    )
    channel = "notification_events"
    enqueue = event.enqueue
    if rendered:
        template(event_type, provider, "Rollback test")
        channel = "notification_rendered_events"
        event.insert()
        unprocessed = models.UnprocessedEvent.objects.get_one(
            filters={"uuid": filters.EQ(event.uuid)}
        )

        def enqueue(session):
            unprocessed.process_event(mock.Mock(), session)

    with psycopg.connect(database_url, autocommit=True) as listener:
        listen(listener, channel)
        session = database.get_session()
        try:
            enqueue(session)
            assert notification(listener, 0.05) is None
            session.rollback()
            assert notification(listener, 0.05) is None
            enqueue(session)
            session.commit()
            received = notification(listener, 1)
            assert received is not None
            assert received.channel == channel
        finally:
            session.close()
            with database.session_manager() as cleanup:
                cleanup.execute(
                    "DELETE FROM rendered_events WHERE event_id = %s",
                    (str(event.uuid),),
                )
                cleanup.execute(
                    "DELETE FROM events WHERE uuid = %s", (str(event.uuid),)
                )


@contextlib.contextmanager
def running(agent):
    agent.should_subscribe_signals = False
    worker = threading.Thread(target=agent.start)
    worker.start()
    try:
        yield
    finally:
        agent.stop()
        worker.join(timeout=4)
        assert not worker.is_alive()


def listener_pid(connection, channel, previous=None):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        row = connection.execute(
            "SELECT pid, backend_xmin FROM pg_stat_activity "
            "WHERE datname = current_database() AND application_name = %s "
            "AND state = 'idle' AND query LIKE 'LISTEN %%'",
            (channel,),
        ).fetchone()
        if row and row[0] != previous:
            assert row[1] is None
            return row[0]
        time.sleep(0.01)
    pytest.fail(f"No idle listener for {channel}")


def test_api_wakes_builder_and_sender_before_fallback(
    http_server, database_url, email_queue, monkeypatch
):
    base, iam = http_server
    event_type, destination = email_queue
    titles = {"Queue wakeup first", "Queue wakeup second"}
    for title in titles:
        template(event_type, destination, title)

    sent = queue.Queue()

    def send(provider, content, user_context):
        if provider.uuid == destination.uuid:
            sent.put(content.title)

    monkeypatch.setattr(models.Provider, "send", send)
    builder = builders.EventBuilderAgent(
        iam_client=mock.Mock(), db_url=database_url, iter_min_period=8
    )
    sender = providers.SMTPAgent(db_url=database_url, iter_min_period=8, butch_size=1)
    pause_sender = threading.Event()
    waiting_after_scan = threading.Event()
    resume_sender = threading.Event()
    process_batch = sender._process_batch

    def process_batch_then_pause():
        has_more = process_batch()
        if pause_sender.is_set() and not has_more:
            pause_sender.clear()
            waiting_after_scan.set()
            assert resume_sender.wait(timeout=10)
        return has_more

    monkeypatch.setattr(sender, "_process_batch", process_batch_then_pause)
    headers = iam.headers(
        [f"exordos_notification.event_type_{event_type.uuid}.create"],
        project_uuid=event_type.project_id,
    )

    with (
        psycopg.connect(database_url, autocommit=True) as observer,
        running(sender),
        running(builder),
    ):
        listener_pid(observer, "notification_events")
        listener_pid(observer, "notification_rendered_events")
        pause_sender.set()
        sender._ready.set()
        try:
            assert waiting_after_scan.wait(timeout=3)
            sender._ready.clear()
            response = requests.post(
                f"{base}/v1/events/",
                json={
                    "project_id": str(event_type.project_id),
                    "event_type": f"/v1/event_types/{event_type.uuid}",
                    "event_params": {},
                    "exchange": {"kind": "Dummy"},
                },
                headers=headers,
                timeout=5,
            )
            assert response.status_code == 201, response.text
            assert sender._ready.wait(timeout=2)
        finally:
            resume_sender.set()
        assert {sent.get(timeout=2), sent.get(timeout=2)} == titles
    assert sent.empty()

    with psycopg.connect(database_url, autocommit=True) as observer:
        assert observer.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() "
            "AND application_name IN ('notification_events', 'notification_rendered_events')"
        ).fetchone() == (0,)


def test_sender_polls_due_work_and_reconnects(database_url, email_queue, monkeypatch):
    _, destination = email_queue
    sent = queue.Queue()

    def send(provider, content, user_context):
        if provider.uuid == destination.uuid:
            sent.put(content.title)

    monkeypatch.setattr(models.Provider, "send", send)
    sender = providers.SMTPAgent(db_url=database_url, iter_min_period=0.4)
    channel = "notification_rendered_events"
    with psycopg.connect(database_url, autocommit=True) as observer, running(sender):
        previous = listener_pid(observer, channel)
        assert observer.execute(
            "SELECT pg_terminate_backend(%s, 1000)", (previous,)
        ).fetchone() == (True,)

        # A plain insert has no wakeup signal. A deadline passed while the listener
        # was down or reconnecting must still be discovered by the periodic check.
        models.RenderedEvent(
            event_id=uuid.uuid4(),
            provider=destination,
            content=models.RenderedEmailContent(title="Fallback"),
            user_context={},
            next_retry_at=datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=0.6),
        ).insert()

        assert listener_pid(observer, channel, previous) != previous
        assert sent.get(timeout=2) == "Fallback"
