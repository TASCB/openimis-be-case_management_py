"""Give the field-facing roles access to case management.

290101 gates the module (and the dashboard); 290205 gates the Account Corrections worklist. Both
were held by IMIS Administrator alone, so everything in case_management was administrator-only.

Council Coordinator and Grievance Officer are the two roles that meet households, and both already
hold the payment read rights (270001, 270301) that the corrections worklist sits alongside.

Unlike the tasaf_payment rights migrations, this one matches roles by name rather than deriving
from a reference right: no existing right is held by exactly these two roles, so there is nothing
to derive from. Matching is by suffix, because role names carry a "TASAF " prefix on some
instances and not on others — an exact match would silently grant nothing there.
"""
from django.db import migrations

RIGHTS = (290101, 290205)
ROLE_SUFFIXES = ('Council Coordinator', 'Grievance Officer')


def _role_filter():
    return " OR ".join(['r."RoleName" ILIKE %s'] * len(ROLE_SUFFIXES))


def grant(apps, schema_editor):
    params_roles = [f'%{name}' for name in ROLE_SUFFIXES]
    with schema_editor.connection.cursor() as cursor:
        for right in RIGHTS:
            cursor.execute(
                f"""
                INSERT INTO "tblRoleRight" ("RoleID", "RightID", "ValidityFrom", "AuditUserId")
                SELECT DISTINCT r."RoleID", %s, NOW(), 1
                FROM "tblRole" r
                WHERE r."ValidityTo" IS NULL
                  AND ({_role_filter()})
                  AND NOT EXISTS (
                      SELECT 1 FROM "tblRoleRight" x
                      WHERE x."RoleID" = r."RoleID" AND x."RightID" = %s
                        AND x."ValidityTo" IS NULL
                  )
                """,
                [right, *params_roles, right],
            )


def revoke(apps, schema_editor):
    """Remove the rights from these roles only; the administrator's own grant is untouched."""
    params_roles = [f'%{name}' for name in ROLE_SUFFIXES]
    with schema_editor.connection.cursor() as cursor:
        for right in RIGHTS:
            cursor.execute(
                f"""
                DELETE FROM "tblRoleRight"
                WHERE "RightID" = %s
                  AND "RoleID" IN (
                      SELECT r."RoleID" FROM "tblRole" r
                      WHERE r."ValidityTo" IS NULL AND ({_role_filter()})
                  )
                """,
                [right, *params_roles],
            )


class Migration(migrations.Migration):

    dependencies = [
        ('case_management', '0007_account_correction_right'),
    ]

    operations = [migrations.RunPython(grant, revoke)]
