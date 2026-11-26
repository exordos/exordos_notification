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


class SMTPAgent(queue_agent.QueueAgent):
    def __init__(self, butch_size=100, iter_min_period=5, **kwargs):
        self._butch_size = butch_size
        super().__init__(
            channel=constants.RENDERED_EVENTS_WAKEUP_CHANNEL,
            iter_min_period=iter_min_period,
            **kwargs,
        )

    def _process_events(self):
        events = models.RenderedEvent.objects.get_all(
            filters={
                "status": filters.NE(models.RenderedEvent.STATUS.ACTIVE.value),
                "next_retry_at": filters.LT(
                    datetime.datetime.now(tz=datetime.timezone.utc)
                ),
            },
            limit=self._butch_size,
        )
        for event in events:
            LOG.info("Processing event: %s", event)
            event.send()
        return len(events) == self._butch_size

    def _process_batch(self):
        ctx = contexts.Context()
        with ctx.session_manager():
            return self._process_events()
