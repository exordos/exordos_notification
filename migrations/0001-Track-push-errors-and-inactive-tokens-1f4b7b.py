# Copyright 2016 Eugene Frolov <eugene@frolov.net.ru>
#
# All Rights Reserved.
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

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0000-init-tables-40a307.py"]

    @property
    def migration_id(self):
        return "1f4b7b14-db6d-4239-8d33-16876701b618"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute(
            "ALTER TABLE events ADD COLUMN errors JSONB NOT NULL DEFAULT '[]'::jsonb"
        )
        session.execute(
            "ALTER TABLE rendered_events ADD COLUMN errors JSONB NOT NULL DEFAULT '[]'::jsonb"
        )
        session.execute("""
            CREATE TABLE inactive_tokens (
                uuid CHAR(36) PRIMARY KEY,
                scope VARCHAR(4096) NOT NULL,
                token_fingerprint CHAR(64) NOT NULL,
                created_at TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                UNIQUE (scope, token_fingerprint)
            )
        """)
        session.execute("""
            CREATE OR REPLACE VIEW incorrect_statuses AS
            SELECT e.uuid, e.uuid AS event,
                e.status AS user_status,
                e.status_description AS user_status_description,
                re.status AS system_status,
                re.status_description AS system_status_description,
                re.errors AS system_errors,
                e.errors AS user_errors
            FROM events e LEFT JOIN rendered_events re ON re.event_id = e.uuid
            WHERE re.uuid IS NOT NULL AND (
                e.status != re.status
                OR e.status_description != re.status_description
                OR e.errors != re.errors
            )
        """)

    def downgrade(self, session):
        session.execute("DROP VIEW incorrect_statuses")
        session.execute("""
            CREATE VIEW incorrect_statuses AS
            SELECT e.uuid, e.uuid AS event, e.status AS user_status,
                e.status_description AS user_status_description,
                re.status AS system_status,
                re.status_description AS system_status_description
            FROM events e LEFT JOIN rendered_events re ON re.event_id = e.uuid
            WHERE re.uuid IS NOT NULL AND (
                e.status != re.status OR e.status_description != re.status_description
            )
        """)
        session.execute("DROP TABLE inactive_tokens")
        session.execute("ALTER TABLE rendered_events DROP COLUMN errors")
        session.execute("ALTER TABLE events DROP COLUMN errors")


migration_step = MigrationStep()
