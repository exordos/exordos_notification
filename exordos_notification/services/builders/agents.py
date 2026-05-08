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

from gcl_looper.services import basic
from restalchemy.common import contexts
from restalchemy.dm import filters

from exordos_notification.dm import models


LOG = logging.getLogger(__name__)


class EventBuilderAgent(basic.BasicService):
    def __init__(self, iam_client, butch_size=100, **kwargs):
        self._iam_client = iam_client
        self._butch_size = butch_size
        super().__init__(**kwargs)

    def _setup(self):
        pass

    def _process_unprocessed_events(self):
        unprocessed_events = models.UnprocessedEvent.objects.get_all(
            filters={
                "next_retry_at": filters.LT(
                    datetime.datetime.now(tz=datetime.timezone.utc)
                ),
            },
            limit=self._butch_size,
        )
        for e in unprocessed_events:
            e.process_event(iam_client=self._iam_client)

    def _sync_event_statuses(self):
        for item in models.IncorrectStatuses.objects.get_all(
            limit=self._butch_size,
        ):
            LOG.info("Syncing item status: %r", item)
            event = item.event
            event.status = item.system_status
            event.status_description = item.system_status_description
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
            LOG.info("Cleaning up event: %r", event)
            event.delete()

    def _iteration(self):
        ctx = contexts.Context()
        with ctx.session_manager():
            self._process_unprocessed_events()
            self._sync_event_statuses()
            self._cleanup()
