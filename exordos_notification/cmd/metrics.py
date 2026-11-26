# Licensed under the Apache License, Version 2.0 (the "License").

import json
import logging
import subprocess
import sys
import time
import urllib.request

from oslo_config import cfg
import psycopg
from restalchemy.common import config_opts as ra_config_opts

from exordos_notification.common import config
from exordos_notification.common import monitoring


LOG = logging.getLogger(__name__)
TIMER = "exordos-notification-metrics.timer"
UNITS = ("user-api", "builder-agent", "mail-agent", "core-agent")
QUEUE_QUERY = """
WITH clock AS (
    SELECT transaction_timestamp() AT TIME ZONE 'UTC' AS now
), items AS (
    SELECT 'builder' AS stage, 'all' AS channel,
           CASE WHEN u.last_retry_at <= c.now THEN 'expired'
                WHEN u.next_retry_at >= u.last_retry_at THEN 'no_retry'
                WHEN u.next_retry_at <= c.now THEN 'ready'
                ELSE 'scheduled' END AS state,
           c.now - e.created_at AS age, c.now - u.next_retry_at AS overdue
    FROM unprocessed_events u
    JOIN events e ON e.uuid = u.event
    CROSS JOIN clock c
    WHERE e.status <> 'ACTIVE'
    UNION ALL
    SELECT 'delivery',
           coalesce(%s::jsonb->>(r.content->>'kind'), 'other'),
           CASE WHEN r.status = 'ACTIVE' THEN 'accepted'
                WHEN r.last_retry_at <= c.now THEN 'expired'
                WHEN r.next_retry_at >= r.last_retry_at THEN 'no_retry'
                WHEN r.next_retry_at <= c.now THEN 'ready'
                ELSE 'scheduled' END,
           c.now - r.created_at, c.now - r.next_retry_at
    FROM rendered_events r
    CROSS JOIN clock c
)
SELECT stage, channel, state, count(*),
       greatest(0, extract(epoch FROM max(age))),
       greatest(0, extract(epoch FROM max(overdue)))
FROM items
GROUP BY stage, channel, state
ORDER BY stage, channel, state
"""


def api_available() -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:8080/v1/", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def service_metrics() -> list[str]:
    samples = []
    for unit in UNITS:
        try:
            output = subprocess.check_output(
                [
                    "systemctl",
                    "show",
                    "-p",
                    "ActiveState",
                    "-p",
                    "NRestarts",
                    f"exordos-notification-{unit}.service",
                ],
                text=True,
                timeout=2,
            )
            fields = dict(
                line.split("=", 1) for line in output.splitlines() if "=" in line
            )
        except (OSError, subprocess.SubprocessError):
            LOG.warning("Cannot read state of the %s unit", unit)
            continue
        label = f'unit="{unit}"'
        active = int(fields.get("ActiveState") == "active")
        samples.append(f"exordos_notification_unit_up{{{label}}} {active}")
        restarts = fields.get("NRestarts", "")
        if restarts.isascii() and restarts.isdecimal():
            samples.append(
                f"exordos_notification_unit_restarts_total{{{label}}} {restarts}"
            )
    return samples


def queue_metrics(connection_url: str) -> list[str]:
    samples = []
    database_up = False
    queue_up = False
    try:
        with psycopg.connect(
            connection_url,
            connect_timeout=3,
            target_session_attrs="primary",
            options="-c default_transaction_read_only=on -c statement_timeout=2000 "
            "-c lock_timeout=500",
        ) as connection:
            connection.execute("SELECT 1")
            database_up = True
            rows = connection.execute(
                QUEUE_QUERY, (json.dumps(monitoring.CHANNELS),)
            ).fetchall()
            queue_up = True
    except (psycopg.Error, ValueError) as exc:
        # Connection and query errors can include credentials or row contents.
        LOG.warning("Notification database collection failed (%s)", type(exc).__name__)
    if queue_up:
        counts = {
            (stage, channel, state): (count, age, overdue)
            for stage, channel, state, count, age, overdue in rows
        }
        channels = sorted(set(monitoring.CHANNELS.values()) | {"other"})
        for stage, transports in (("builder", ("all",)), ("delivery", channels)):
            states = ["ready", "scheduled", "expired", "no_retry"]
            if stage == "delivery":
                states.append("accepted")
            for channel in transports:
                for state in states:
                    count, age, overdue = counts.get((stage, channel, state), (0, 0, 0))
                    labels = f'stage="{stage}",channel="{channel}",state="{state}"'
                    samples.append(
                        f"exordos_notification_queue_items{{{labels}}} {count}"
                    )
                    if state in ("ready", "scheduled"):
                        samples.append(
                            f"exordos_notification_queue_oldest_seconds{{{labels}}} {age}"
                        )
                    if state == "ready":
                        samples.append(
                            f"exordos_notification_queue_overdue_seconds{{{labels}}} {overdue}"
                        )
    samples.append(f"exordos_notification_database_up {int(database_up)}")
    samples.append(f"exordos_notification_queue_collection_up {int(queue_up)}")
    return samples


def main() -> None:
    if not monitoring.ENABLED.is_file():
        with monitoring.lock():
            if monitoring.ENABLED.is_file():
                return
            for path in (monitoring.SNAPSHOT, monitoring.BUILDER, monitoring.DELIVERY):
                path.unlink(missing_ok=True)
            subprocess.run(
                ["systemctl", "disable", "--now", TIMER], check=True, timeout=10
            )
        return

    ra_config_opts.register_posgresql_db_opts(cfg.CONF)
    config.parse(sys.argv[1:])
    samples = queue_metrics(cfg.CONF.db.connection_url)
    samples.append("# TYPE exordos_notification_unit_restarts_total counter")
    samples.extend(service_metrics())
    samples.append(f"exordos_notification_api_up {int(api_available())}")
    samples.append(
        f"exordos_notification_snapshot_timestamp_seconds {int(time.time())}"
    )
    with monitoring.lock():
        if monitoring.ENABLED.is_file():
            monitoring.write_metrics(monitoring.SNAPSHOT, samples)


if __name__ == "__main__":
    main()
