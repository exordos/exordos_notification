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


class MigrationStep(migrations.AbstractMigrationStep):

    def __init__(self):
        self._depends = [
            "0002-user-devices-and-notification-routes-6d0b1e.py"
        ]

    @property
    def migration_id(self):
        return "9c4e2a2d-d8bc-4cbd-b36d-51d434b9ef17"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        expressions = [
            """
            CREATE TABLE "app_provider_mappings" (
                "uuid" CHAR(36) PRIMARY KEY,
                "status" enum_status_active NOT NULL DEFAULT 'ACTIVE',
                "project_id" CHAR(36) NOT NULL,
                "created_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                "updated_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                "app_id" VARCHAR(128) NOT NULL,
                "provider_id" CHAR(36) NOT NULL REFERENCES providers(uuid),

                CONSTRAINT "app_provider_mappings_project_app_provider_uniq"
                UNIQUE ("project_id", "app_id", "provider_id")
            );
            """,
            """
            CREATE INDEX "app_provider_mappings_project_app_idx"
            ON "app_provider_mappings" ("project_id", "app_id");
            """,
            """
            CREATE INDEX "app_provider_mappings_provider_idx"
            ON "app_provider_mappings" ("provider_id");
            """,
            """
            CREATE TABLE "event_type_notification_targets" (
                "uuid" CHAR(36) PRIMARY KEY,
                "status" enum_status_active NOT NULL DEFAULT 'ACTIVE',
                "project_id" CHAR(36) NOT NULL,
                "created_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                "updated_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                "event_type_uuid" CHAR(36) NOT NULL REFERENCES event_types(uuid),
                "app_id" VARCHAR(128) NOT NULL,
                "provider_id" CHAR(36) NOT NULL REFERENCES providers(uuid),

                CONSTRAINT "event_type_notification_targets_uniq"
                UNIQUE (
                    "project_id",
                    "event_type_uuid",
                    "app_id",
                    "provider_id"
                )
            );
            """,
            """
            CREATE INDEX "event_type_notification_targets_project_event_idx"
            ON "event_type_notification_targets" (
                "project_id",
                "event_type_uuid"
            );
            """,
            """
            CREATE INDEX "event_type_notification_targets_provider_idx"
            ON "event_type_notification_targets" ("provider_id");
            """,
        ]

        for expression in expressions:
            session.execute(expression)

    def downgrade(self, session):
        expressions = [
            """
            DROP TABLE IF EXISTS "event_type_notification_targets";
            """,
            """
            DROP TABLE IF EXISTS "app_provider_mappings";
            """,
        ]

        for expression in expressions:
            session.execute(expression)


migration_step = MigrationStep()
