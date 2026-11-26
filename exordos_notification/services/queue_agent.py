# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.

import abc
import contextlib
import logging
import threading

from gcl_looper.services import basic
import psycopg
from psycopg import sql


LOG = logging.getLogger(__name__)


class QueueAgent(basic.BasicService):
    def __init__(self, *, channel, db_url=None, iter_min_period=1, **kwargs):
        super().__init__(iter_min_period=0 if db_url else iter_min_period, **kwargs)
        self._channel = channel
        self._db_url = db_url
        self._poll_interval = iter_min_period
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._listener = None

    def _setup(self):
        super()._setup()
        if self._db_url:
            self._listener = threading.Thread(
                target=self._listen, name=self._channel, daemon=True
            )
            self._listener.start()

    def _listen(self):
        statement = sql.SQL("LISTEN {}").format(sql.Identifier(self._channel))
        while not self._stopping.is_set():
            try:
                with psycopg.connect(
                    self._db_url,
                    autocommit=True,
                    connect_timeout=2,
                    application_name=self._channel,
                    keepalives_idle=5,
                    keepalives_interval=2,
                    keepalives_count=2,
                    tcp_user_timeout=5000,
                ) as connection:
                    connection.execute(statement)
                    self._ready.set()
                    while not self._stopping.is_set():
                        with contextlib.closing(
                            connection.notifies(timeout=0.5)
                        ) as notifications:
                            for _ in notifications:
                                self._ready.set()
                                if self._stopping.is_set():
                                    return
            except psycopg.Error as error:
                if not self._stopping.is_set():
                    LOG.warning(
                        "Cannot listen on %s (%s); queue polling continues",
                        self._channel,
                        type(error).__name__,
                    )
                    self._stopping.wait(self._poll_interval)

    def _iteration(self):
        # Keep signals arriving while the queue is being read or processed.
        self._ready.clear()
        if self._stopping.is_set():
            return
        try:
            has_more = self._process_batch()
        except Exception:
            if self._db_url:
                self._stopping.wait(self._poll_interval)
            raise
        if self._db_url and not has_more:
            self._ready.wait(self._poll_interval)

    @abc.abstractmethod
    def _process_batch(self):
        """Process one batch and return whether to read the queue again immediately."""

    def stop(self):
        super().stop()
        self._stopping.set()
        self._ready.set()

    def _finish(self):
        self._stopping.set()
        if self._listener is not None:
            self._listener.join(timeout=3)
        super()._finish()
