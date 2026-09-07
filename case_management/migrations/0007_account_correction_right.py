"""Grant the Account Corrections right (290205).

The page is gated on it, so without the grant it is invisible to everyone.
Granted to whoever already holds 290201 (payment-change search) -- the existing answer to
"who works on payment account data".
"""
from django.db import migrations

RIGHT = 290205
REFERENCE_RIGHT = 290201


def grant(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            '''
            INSERT INTO "tblRoleRight" ("RoleID", "RightID", "ValidityFrom", "AuditUserId")
            SELECT DISTINCT rr."RoleID", %s, NOW(), 1
            FROM "tblRoleRight" rr
            WHERE rr."RightID" = %s AND rr."ValidityTo" IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM "tblRoleRight" x
                  WHERE x."RoleID" = rr."RoleID" AND x."RightID" = %s AND x."ValidityTo" IS NULL
              )
            ''',
            [RIGHT, REFERENCE_RIGHT, RIGHT],
        )


def revoke(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('DELETE FROM "tblRoleRight" WHERE "RightID" = %s', [RIGHT])


class Migration(migrations.Migration):
    dependencies = [('case_management', '0001_initial')]
    operations = [migrations.RunPython(grant, revoke)]
