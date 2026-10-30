#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License.

import datetime
import ipaddress
import os
import pathlib
import ssl
import threading
import time
import uuid
import wsgiref.simple_server

import jwt
import psycopg
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from gcl_iam import algorithms
from restalchemy.storage.sql import engines, migrations

from exordos_notification.user_api.api import app


@pytest.fixture(scope="session")
def database():
    url = os.environ.get("NOTIFICATION_TEST_DB_URL") or os.environ.get("DATABASE_URI")
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
    redirect_post_target = None

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

    def headers(
        self, permissions=(), user_uuid=None, project_uuid=None, project_scoped=True
    ):
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
                "test_project": str(project_uuid or uuid.uuid4())
                if project_scoped
                else None,
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
def http_server(database, tmp_path_factory):
    # Exercise the authenticated WSGI API with TLS certificate verification.
    tls_dir = tmp_path_factory.mktemp("notification-tls")
    private_key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )
    cert_file, key_file = tls_dir / "server.crt", tls_dir / "server.key"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_file.chmod(0o600)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_file, key_file)
    original_ca = os.environ.get("REQUESTS_CA_BUNDLE")
    os.environ["REQUESTS_CA_BUNDLE"] = str(cert_file)
    driver = TestIamDriver()
    application = app.build_wsgi_application(driver)

    def dispatch(environ, start_response):
        if driver.redirect_post_target and environ["REQUEST_METHOD"] == "POST":
            start_response(
                "307 Temporary Redirect",
                [("Location", driver.redirect_post_target), ("Content-Length", "0")],
            )
            return [b""]
        return application(environ, start_response)

    server = wsgiref.simple_server.make_server(
        "127.0.0.1",
        0,
        dispatch,
        handler_class=QuietHandler,
    )
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_port}", driver
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        if original_ca is None:
            os.environ.pop("REQUESTS_CA_BUNDLE", None)
        else:
            os.environ["REQUESTS_CA_BUNDLE"] = original_ca
