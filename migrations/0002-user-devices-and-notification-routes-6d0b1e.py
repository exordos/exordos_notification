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


LEGACY_UUID = "00000000-0000-0000-0000-000000000000"
LEGACY_APP_ID = "legacy"


class MigrationStep(migrations.AbstractMigrationStep):

    def __init__(self):
        self._depends = ["0001-fcm-installation-model-232cd7.py"]

    @property
    def migration_id(self):
        return "6d0b1ef2-550b-4c21-8b11-7191fbac0d7a"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        expressions = [
            """
            ALTER TYPE "enum_status_active" ADD VALUE IF NOT EXISTS 'INACTIVE';
            """,
            """
            DROP INDEX IF EXISTS "installations_user_idx";
            """,
            """
            DROP INDEX IF EXISTS "installations_token_idx";
            """,
            """
            DROP INDEX IF EXISTS "installations_installation_id_idx";
            """,
            """
            ALTER TABLE "installations" RENAME TO "user_devices";
            """,
            """
            ALTER TABLE "user_devices"
            RENAME COLUMN "user_id" TO "user_uuid";
            """,
            """
            ALTER TABLE "user_devices"
            RENAME COLUMN "push_token" TO "fcm_token";
            """,
            f"""
            ALTER TABLE "user_devices"
            ADD COLUMN "project_id" CHAR(36) NOT NULL DEFAULT '{LEGACY_UUID}',
            ADD COLUMN "app_id" VARCHAR(128) NOT NULL DEFAULT '{LEGACY_APP_ID}',
            ADD COLUMN "provider_id" CHAR(36) NOT NULL DEFAULT '{LEGACY_UUID}';
            """,
            """
            ALTER TABLE "user_devices"
            DROP COLUMN "installation_id";
            """,
            """
            ALTER TABLE "user_devices"
            ALTER COLUMN "app_version" TYPE VARCHAR(64),
            ALTER COLUMN "os_version" TYPE VARCHAR(64),
            ALTER COLUMN "device_model" TYPE VARCHAR(128);
            """,
            """
            ALTER TABLE "user_devices"
            ALTER COLUMN "project_id" DROP DEFAULT,
            ALTER COLUMN "app_id" DROP DEFAULT,
            ALTER COLUMN "provider_id" DROP DEFAULT;
            """,
            """
            UPDATE "user_devices"
            SET
                "status" = 'INACTIVE',
                "platform" = 'android'
            WHERE "platform" NOT IN ('android', 'ios');
            """,
            """
            ALTER TABLE "user_devices"
            ADD CONSTRAINT "user_devices_project_fcm_token_uniq"
            UNIQUE ("project_id", "fcm_token");
            """,
            """
            CREATE INDEX "user_devices_project_user_idx"
            ON "user_devices" ("project_id", "user_uuid");
            """,
            """
            CREATE INDEX "user_devices_project_app_idx"
            ON "user_devices" ("project_id", "app_id");
            """,
            """
            CREATE TABLE "user_notification_routes" (
                "uuid" CHAR(36) PRIMARY KEY,
                "status" enum_status_active NOT NULL DEFAULT 'ACTIVE',
                "project_id" CHAR(36) NOT NULL,
                "user_uuid" CHAR(36) NOT NULL,
                "event_type_uuid" CHAR(36) NOT NULL,
                "app_id" VARCHAR(128) NOT NULL,
                "provider_id" CHAR(36) NOT NULL,

                CONSTRAINT "user_notification_routes_project_user_event_uniq"
                UNIQUE ("project_id", "user_uuid", "event_type_uuid")
            );
            """,
        ]

        for expression in expressions:
            session.execute(expression)

    def downgrade(self, session):
        expressions = [
            """
            DROP TABLE IF EXISTS "user_notification_routes";
            """,
            """
            DROP INDEX IF EXISTS "user_devices_project_user_idx";
            """,
            """
            DROP INDEX IF EXISTS "user_devices_project_app_idx";
            """,
            """
            ALTER TABLE "user_devices"
            DROP CONSTRAINT IF EXISTS "user_devices_project_fcm_token_uniq";
            """,
            """
            ALTER TABLE "user_devices"
            ADD COLUMN "installation_id" VARCHAR(128);
            """,
            """
            UPDATE "user_devices" SET "installation_id" = "uuid";
            """,
            """
            ALTER TABLE "user_devices"
            ALTER COLUMN "installation_id" SET NOT NULL;
            """,
            """
            ALTER TABLE "user_devices"
            ALTER COLUMN "app_version" TYPE CHAR(16),
            ALTER COLUMN "os_version" TYPE CHAR(16),
            ALTER COLUMN "device_model" TYPE CHAR(16);
            """,
            """
            ALTER TABLE "user_devices"
            DROP COLUMN "project_id",
            DROP COLUMN "app_id",
            DROP COLUMN "provider_id";
            """,
            """
            ALTER TABLE "user_devices"
            RENAME COLUMN "fcm_token" TO "push_token";
            """,
            """
            ALTER TABLE "user_devices"
            RENAME COLUMN "user_uuid" TO "user_id";
            """,
            """
            ALTER TABLE "user_devices" RENAME TO "installations";
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


migration_step = MigrationStep()
