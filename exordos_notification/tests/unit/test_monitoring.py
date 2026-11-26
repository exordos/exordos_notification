# Licensed under the Apache License, Version 2.0 (the "License").

from unittest import mock

import psycopg

from exordos_notification.cmd import metrics
from exordos_notification.common import monitoring


def test_empty_queue_and_failed_query_are_distinct():
    with mock.patch.object(metrics.psycopg, "connect") as connect:
        connection = connect.return_value.__enter__.return_value
        connection.execute.return_value.fetchall.return_value = []
        samples = metrics.queue_metrics("test")
        assert "exordos_notification_queue_collection_up 1" in samples
        assert (
            'exordos_notification_queue_items{stage="builder",channel="all",state="ready"} 0'
            in samples
        )

        connection.execute.side_effect = [mock.Mock(), psycopg.errors.QueryCanceled()]
        assert metrics.queue_metrics("test") == [
            "exordos_notification_database_up 1",
            "exordos_notification_queue_collection_up 0",
        ]


def test_worker_publishes_only_when_monitoring_is_enabled(tmp_path, monkeypatch):
    enabled = tmp_path / "enabled"
    output = tmp_path / "worker.prom"
    monkeypatch.setattr(monitoring, "ENABLED", enabled)
    monkeypatch.setattr(monitoring, "LOCK", tmp_path / "worker.lock")
    worker = monitoring.WorkerMetrics("delivery", output)

    worker.cycle_completed()
    assert not output.exists()

    enabled.touch()
    worker.cycle_completed()
    assert (
        'exordos_notification_worker_last_success_timestamp_seconds{stage="delivery"}'
        in output.read_text()
    )
    assert not list(tmp_path.glob(".*.prom.*"))


def test_uninstall_removes_metrics_and_stops_the_timer(tmp_path, monkeypatch):
    monkeypatch.setattr(monitoring, "ENABLED", tmp_path / "missing")
    monkeypatch.setattr(monitoring, "LOCK", tmp_path / "worker.lock")
    for name in ("SNAPSHOT", "BUILDER", "DELIVERY"):
        path = tmp_path / (name.lower() + ".prom")
        path.write_text("old metrics")
        monkeypatch.setattr(monitoring, name, path)
    with mock.patch.object(metrics.subprocess, "run") as systemctl:
        metrics.main()
    assert not list(tmp_path.glob("*.prom"))
    systemctl.assert_called_once_with(
        ["systemctl", "disable", "--now", metrics.TIMER], check=True, timeout=10
    )
