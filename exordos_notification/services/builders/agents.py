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

from restalchemy.common import contexts
from restalchemy.dm import filters

from exordos_notification.common import constants
from exordos_notification.dm import models
from exordos_notification.services import queue_agent


LOG = logging.getLogger(__name__)


class EventBuilderAgent(queue_agent.QueueAgent):
    def __init__(self, iam_client, butch_size=100, **kwargs):
        self._iam_client = iam_client
        self._butch_size = butch_size
        super().__init__(channel=constants.EVENTS_WAKEUP_CHANNEL, **kwargs)

    def _process_unprocessed_events(self, session):
        unprocessed_events = models.UnprocessedEvent.objects.get_all(
            filters={
                "next_retry_at": filters.LT(
                    datetime.datetime.now(tz=datetime.timezone.utc)
                ),
            },
            limit=self._butch_size,
        )
        progressed = False
        for event in unprocessed_events:
            if event.process_event(iam_client=self._iam_client, session=session):
                progressed = True
        return len(unprocessed_events) == self._butch_size and progressed

    def _sync_event_statuses(self):
        for item in models.IncorrectStatuses.objects.get_all(
            limit=self._butch_size,
        ):
            LOG.info("Syncing event status: %s", item.event.uuid)
            event = item.event
            event.status = item.system_status
            event.status_description = item.system_status_description
            event.errors = item.system_errors
            event.update()

    def _cleanup(self):
        events = models.Event.objects.get_all(
            filters={
                "last_retry_at": filters.LT(
                    datetime.datetime.now(tz=datetime.timezone.utc)
                )
            },
            limit=self._butch_size,
        )
        rendered_events = models.RenderedEvent.objects.get_all(
            filters={
                "last_retry_at": filters.LT(
                    datetime.datetime.now(tz=datetime.timezone.utc)
                )
            },
            limit=self._butch_size,
        )

        for event in events + rendered_events:
            LOG.info("Cleaning up event: %s", event.uuid)
            event.delete()

    def _process_batch(self):
        ctx = contexts.Context()
        with ctx.session_manager() as session:
            has_more = self._process_unprocessed_events(session)
            self._sync_event_statuses()
            self._cleanup()
        return has_more
