"""Payment Approvers see and decide Pending updates (290501, 290502).

Granted to holders of 270303 (the payment checkers). Reverse spares roles that also hold 290205.
"""
from django.db import migrations

RIGHTS = (290501, 290502)
REFERENCE = 270303
PRE_EXISTING_MARKER = 290205


def grant(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for right in RIGHTS:
            cursor.execute(
                """
                INSERT INTO "tblRoleRight" ("RoleID", "RightID", "ValidityFrom", "AuditUserId")
                SELECT DISTINCT rr."RoleID", %s, NOW(), 1
                FROM "tblRoleRight" rr
                WHERE rr."RightID" = %s AND rr."ValidityTo" IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM "tblRoleRight" x
                      WHERE x."RoleID" = rr."RoleID" AND x."RightID" = %s AND x."ValidityTo" IS NULL
                  )
                """,
                [right, REFERENCE, right],
            )


def revoke(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM "tblRoleRight" rr
            WHERE rr."RightID" IN %s
              AND EXISTS (SELECT 1 FROM "tblRoleRight" a
                          WHERE a."RoleID" = rr."RoleID" AND a."RightID" = %s AND a."ValidityTo" IS NULL)
              AND NOT EXISTS (SELECT 1 FROM "tblRoleRight" m
                              WHERE m."RoleID" = rr."RoleID" AND m."RightID" = %s AND m."ValidityTo" IS NULL)
            """,
            [RIGHTS, REFERENCE, PRE_EXISTING_MARKER],
        )


class Migration(migrations.Migration):

    dependencies = [
        ('case_management', '0010_pending_update_is_proposal'),
    ]

    operations = [migrations.RunPython(grant, revoke)]
