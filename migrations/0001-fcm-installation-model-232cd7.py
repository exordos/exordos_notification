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
        return "232cd714-c3c7-448e-a852-c0787b20b5ef"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        expressions = [
            """
            CREATE TABLE "installations" (
                "uuid" CHAR(36) PRIMARY KEY,
                "status" enum_status_active NOT NULL DEFAULT 'ACTIVE',
                "created_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                "updated_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),

                "installation_id" VARCHAR(128) NOT NULL,
                "user_id" CHAR(36) NOT NULL,
                "platform" VARCHAR(32) NOT NULL,
                "push_token" TEXT NOT NULL,
                "app_version" CHAR(16) NOT NULL,
                "os_version" CHAR(16) NOT NULL,
                "device_model" CHAR(16) NOT NULL
            );
            """,
            """
            CREATE INDEX "installations_user_idx"
            ON "installations" ("user_id");
            """,
            """
            CREATE INDEX "installations_token_idx"
            ON "installations" ("push_token");
            """,
            """
            CREATE INDEX "installations_installation_id_idx"
            ON "installations" ("installation_id");
            """,
        ]

        for expression in expressions:
            session.execute(expression)

    def downgrade(self, session):
        self._delete_table_if_exists(session, "installations")


migration_step = MigrationStep()
