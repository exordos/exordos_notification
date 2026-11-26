# Licensed under the Apache License, Version 2.0 (the "License").

import contextlib
import fcntl
import logging
import os
import pathlib
import tempfile
import time

import prometheus_client


LOG = logging.getLogger(__name__)
ENABLED = pathlib.Path("/etc/exordos_notification/monitoring.enabled")
TEXTFILE_DIR = pathlib.Path("/var/lib/exordos/node_exporter")
SNAPSHOT = TEXTFILE_DIR / "notification.prom"
BUILDER = TEXTFILE_DIR / "notification_builder.prom"
DELIVERY = TEXTFILE_DIR / "notification_delivery.prom"
LOCK = TEXTFILE_DIR / "notification.lock"

CHANNELS = {
    "rendered_email": "smtp",
    "rendered_firebase_android": "fcm",
    "rendered_apns": "apns",
    "rendered_notification_relay": "relay",
    "rendered_zulip_stream_message": "zulip",
    "rendered_zulip_direct_message": "zulip",
}


@contextlib.contextmanager
def lock(nonblocking: bool = False):
    # Keep file publication and removal in the same critical section.
    with LOCK.open("a") as lock_file:
        operation = fcntl.LOCK_EX | fcntl.LOCK_NB if nonblocking else fcntl.LOCK_EX
        fcntl.flock(lock_file, operation)
        yield


def write_metrics(path: pathlib.Path, samples: list[str]) -> None:
    with tempfile.NamedTemporaryFile(
        dir=path.parent,
        prefix=f".{path.name}.",
        mode="w",
        encoding="ascii",
        delete=False,
    ) as output:
        temporary_path = pathlib.Path(output.name)
        try:
            output.write("\n".join(samples) + "\n")
            output.flush()
            os.fchmod(output.fileno(), 0o644)
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)


class WorkerMetrics:
    def __init__(self, stage: str, path: pathlib.Path) -> None:
        self._path = path
        self._last_write = None
        self.registry = prometheus_client.CollectorRegistry()
        self._last_success = prometheus_client.Gauge(
            "exordos_notification_worker_last_success_timestamp_seconds",
            "Time of the latest successful worker cycle",
            ("stage",),
            registry=self.registry,
        ).labels(stage)

    def cycle_completed(self) -> None:
        if not ENABLED.is_file():
            return
        self._last_success.set(time.time())
        now = time.monotonic()
        if self._last_write is not None and now - self._last_write < 15:
            return
        try:
            with lock(nonblocking=True):
                if ENABLED.is_file():
                    payload = prometheus_client.generate_latest(self.registry).decode(
                        "ascii"
                    )
                    write_metrics(self._path, payload.splitlines())
                    self._last_write = now
        except BlockingIOError:
            return
        except OSError as error:
            LOG.warning("Cannot publish worker metrics (%s)", type(error).__name__)
