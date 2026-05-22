# Copyright 2026 Genesis Corporation
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


LEGACY_APP_ID = "legacy"


class MigrationStep(migrations.AbstractMigrationStep):

    def __init__(self):
        self._depends = [
            "0003-notification-routing-allowlists-9c4e2a.py"
        ]

    @property
    def migration_id(self):
        return "1b8f72b7-6844-4f52-b566-9acbc4d850ef"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        expressions = [
            f"""
            ALTER TABLE "events"
            ADD COLUMN "app_id" VARCHAR(128) NOT NULL DEFAULT '{LEGACY_APP_ID}';
            """,
            f"""
            ALTER TABLE "rendered_events"
            ADD COLUMN "project_id" CHAR(36),
            ADD COLUMN "app_id" VARCHAR(128) NOT NULL DEFAULT '{LEGACY_APP_ID}',
            ADD COLUMN "event_type_uuid" CHAR(36);
            """,
            """
            UPDATE "rendered_events" re
            SET
                "project_id" = e."project_id",
                "event_type_uuid" = e."event_type"
            FROM "events" e
            WHERE re."event_id" = e."uuid";
            """,
            """
            ALTER TABLE "rendered_events"
            ALTER COLUMN "project_id" SET NOT NULL,
            ALTER COLUMN "event_type_uuid" SET NOT NULL;
            """,
            """
            ALTER TABLE "events"
            ALTER COLUMN "app_id" DROP DEFAULT;
            """,
            """
            ALTER TABLE "rendered_events"
            ALTER COLUMN "app_id" DROP DEFAULT;
            """,
            """
            CREATE INDEX "rendered_events_project_idx"
            ON "rendered_events" ("project_id");
            """,
            """
            CREATE INDEX "rendered_events_event_type_idx"
            ON "rendered_events" ("event_type_uuid");
            """,
        ]

        for expression in expressions:
            session.execute(expression)

    def downgrade(self, session):
        expressions = [
            """
            DROP INDEX IF EXISTS "rendered_events_project_idx";
            """,
            """
            DROP INDEX IF EXISTS "rendered_events_event_type_idx";
            """,
            """
            ALTER TABLE "rendered_events"
            DROP COLUMN "project_id",
            DROP COLUMN "app_id",
            DROP COLUMN "event_type_uuid";
            """,
            """
            ALTER TABLE "events"
            DROP COLUMN "app_id";
            """,
        ]

        for expression in expressions:
            session.execute(expression)


migration_step = MigrationStep()
