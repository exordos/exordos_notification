#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License.

import os
import pathlib
import threading
import time
import uuid
import wsgiref.simple_server

import jwt
import psycopg
import pytest
from gcl_iam import algorithms
from restalchemy.storage.sql import engines, migrations

from exordos_notification.common import constants
from exordos_notification.dm import models
from exordos_notification.user_api.api import app


@pytest.fixture(scope="session")
def database():
    url = os.environ.get("NOTIFICATION_TEST_DB_URL")
    if not url:
        pytest.skip(
            "NOTIFICATION_TEST_DB_URL must select a dedicated empty test database"
        )
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    engines.engine_factory.configure_factory(db_url=url)
    path = pathlib.Path(__file__).resolve().parents[3] / "migrations"
    migrator = migrations.MigrationEngine(migrations_path=str(path))
    migrator.apply_migration(migrator.get_latest_migration())
    engine = engines.engine_factory.get_engine()
    try:
        yield engine
    finally:
        engine._pool.close()
        engines.engine_factory.destroy_all_engines()


class TestIamDriver:
    # The JWT signature and service policies are real; only IAM introspection is local.
    key = os.urandom(32)

    def get_algorithm(self, token_info):
        return algorithms.HS256(key=self.key)

    def get_introspection_info(self, token_info, otp_code=None):
        claims = token_info.token_info
        return {
            "user_info": {"uuid": str(token_info.user_uuid), "type": "user"},
            "project_id": claims["test_project"],
            "otp_verified": True,
            "permissions": claims["test_permissions"],
        }

    def headers(self, permissions=(), user_uuid=None, project_uuid=None):
        now = int(time.time())
        token = jwt.encode(
            {
                "aud": "notification-test",
                "iss": "test",
                "typ": "access",
                "sub": str(user_uuid or uuid.uuid4()),
                "jti": str(uuid.uuid4()),
                "iat": now,
                "auth_time": now,
                "exp": now + 3600,
                "test_project": str(project_uuid or uuid.uuid4()),
                "test_permissions": list(permissions),
            },
            self.key,
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}


class QuietHandler(wsgiref.simple_server.WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


@pytest.fixture(scope="session")
def http_server(database):
    for kind, event_uuid in constants.PUSH_EVENT_TYPES.items():
        models.EventType(uuid=event_uuid, name=kind, project_id=uuid.uuid4()).insert()
    driver = TestIamDriver()
    server = wsgiref.simple_server.make_server(
        "127.0.0.1",
        0,
        app.build_wsgi_application(driver),
        handler_class=QuietHandler,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", driver
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
