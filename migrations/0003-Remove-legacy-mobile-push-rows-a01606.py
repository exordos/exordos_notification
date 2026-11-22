# Copyright 2026 Genesis Corporation
# Licensed under the Apache License, Version 2.0 (the "License").

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0002-Deactivate-legacy-mobile-push-templates-29a44a.py"]

    @property
    def migration_id(self):
        return "a01606cb-bc60-4e05-8c22-f290bde00a8b"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute("""
            WITH removed AS (
                DELETE FROM rendered_events
                WHERE content ->> 'kind' = 'rendered_mobile_push'
                RETURNING event_id
            )
            UPDATE events e
            SET next_retry_at = e.last_retry_at + INTERVAL '365 days',
                status = CASE WHEN e.status = 'ACTIVE' THEN e.status ELSE 'ERROR' END,
                status_description = CASE WHEN e.status = 'ACTIVE'
                    THEN e.status_description ELSE 'legacy_mobile_push_removed' END,
                errors = CASE WHEN e.status = 'ACTIVE' THEN e.errors
                    ELSE '[{"code":"legacy_mobile_push_removed","permanent":true}]' END
            WHERE e.uuid IN (SELECT event_id FROM removed)
              AND NOT EXISTS (
                SELECT 1 FROM rendered_events re
                WHERE re.event_id = e.uuid
                  AND re.content ->> 'kind' IS DISTINCT FROM 'rendered_mobile_push'
              )
        """)

    def downgrade(self, session):
        # Retired delivery data is discarded during the upgrade.
        pass


migration_step = MigrationStep()
