from django.db import migrations

NEW_RIGHTS = (290301,)
ROLE_SUFFIXES = ('PAA Facilitator', 'Council Coordinator')


def _role_filter():
    return " OR ".join(['r."RoleName" ILIKE %s'] * len(ROLE_SUFFIXES))


def grant(apps, schema_editor):
    params_roles = [f'%{name}' for name in ROLE_SUFFIXES]
    with schema_editor.connection.cursor() as cursor:
        for right in NEW_RIGHTS:
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
    params_roles = [f'%{name}' for name in ROLE_SUFFIXES]
    with schema_editor.connection.cursor() as cursor:
        for right in NEW_RIGHTS:
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
        ('case_management', '0016_tmo_case_views_and_field_training_view'),
    ]

    operations = [migrations.RunPython(grant, revoke)]
