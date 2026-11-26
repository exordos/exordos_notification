#    Copyright 2025 Genesis Corporation.
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

import datetime
import logging
import time

from gcl_looper.services import basic
import prometheus_client
from restalchemy.common import contexts
from restalchemy.dm import filters

from exordos_notification.common import monitoring
from exordos_notification.dm import models


LOG = logging.getLogger(__name__)


class SMTPAgent(basic.BasicService):
    def __init__(self, butch_size=100, iter_min_period=5, **kwargs):
        self._butch_size = butch_size
        self._metrics = monitoring.WorkerMetrics("delivery", monitoring.DELIVERY)
        self._attempts = prometheus_client.Counter(
            "exordos_notification_delivery_attempts_total",
            "Attempts to send a rendered event or check its relay status",
            ("channel", "outcome"),
            registry=self._metrics.registry,
        )
        self._duration = prometheus_client.Histogram(
            "exordos_notification_delivery_attempt_duration_seconds",
            "Time spent sending an event or checking its relay status",
            ("channel", "outcome"),
            registry=self._metrics.registry,
        )
        self._queue_to_accept = prometheus_client.Histogram(
            "exordos_notification_queue_to_accept_seconds",
            "Time between rendering and provider or relay confirmation",
            ("channel",),
            buckets=(0.1, 0.5, 1, 3, 5, 10, 30, 60, 120, 300, 900, 3600),
            registry=self._metrics.registry,
        )
        super().__init__(iter_min_period=iter_min_period, **kwargs)

    def _setup(self):
        pass

    def _process_events(self):
        enabled = monitoring.ENABLED.is_file()
        for event in models.RenderedEvent.objects.get_all(
            filters={
                "status": filters.NE(models.RenderedEvent.STATUS.ACTIVE.value),
                "next_retry_at": filters.LT(
                    datetime.datetime.now(tz=datetime.timezone.utc)
                ),
            },
            limit=self._butch_size,
        ):
            LOG.info("Processing event: %s", event)
            started = time.monotonic() if enabled else None
            event.send()
            if started is None:
                continue
            channel = monitoring.CHANNELS.get(event.content.KIND, "other")
            outcome = self._outcome(event)
            self._attempts.labels(channel, outcome).inc()
            self._duration.labels(channel, outcome).observe(time.monotonic() - started)
            if outcome == "accepted":
                age = datetime.datetime.now(tz=datetime.timezone.utc) - event.created_at
                self._queue_to_accept.labels(channel).observe(
                    max(0, age.total_seconds())
                )

    @staticmethod
    def _outcome(event: models.RenderedEvent) -> str:
        if event.status == event.STATUS.ACTIVE.value:
            return "accepted"
        if event.status == event.STATUS.IN_PROGRESS.value:
            return "pending"
        if any(
            error.get("code") == "registration_token_inactive" for error in event.errors
        ):
            return "inactive_token"
        if event.errors and all(error.get("permanent") for error in event.errors):
            return "permanent_error"
        return "retryable_error"

    def _iteration(self):
        ctx = contexts.Context()
        with ctx.session_manager():
            self._process_events()
        self._metrics.cycle_completed()
