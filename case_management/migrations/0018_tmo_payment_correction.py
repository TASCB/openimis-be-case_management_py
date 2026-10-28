"""TMO corrects payment accounts sent to Case after an unapplied payment, as PAA Facilitator and
Council Coordinator (PSSNC) already do. The PCT keeps view only."""
from django.db import migrations

RIGHT = 290203
ROLE = 'TMO'


def grant(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO "tblRoleRight" ("RoleID", "RightID", "ValidityFrom", "AuditUserId")
            SELECT r."RoleID", %s, NOW(), 1
            FROM "tblRole" r
            WHERE r."ValidityTo" IS NULL AND r."RoleName" = %s
              AND NOT EXISTS (
                  SELECT 1 FROM "tblRoleRight" x
                  WHERE x."RoleID" = r."RoleID" AND x."RightID" = %s AND x."ValidityTo" IS NULL
              )
            """,
            [RIGHT, ROLE, RIGHT],
        )


def revoke(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM "tblRoleRight" WHERE "RightID" = %s
              AND "RoleID" IN (SELECT r."RoleID" FROM "tblRole" r WHERE r."RoleName" = %s)
            """,
            [RIGHT, ROLE],
        )


class Migration(migrations.Migration):

    dependencies = [
        ('case_management', '0017_deactivation_search_for_field_roles'),
    ]

    operations = [migrations.RunPython(grant, revoke)]
