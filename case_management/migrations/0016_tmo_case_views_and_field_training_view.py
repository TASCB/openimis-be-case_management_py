from django.db import migrations

GRANTS = {
    'TMO': (290201, 290205, 290301, 290401),
    'PAA Facilitator': (210101, 210201, 210301, 210401, 210501, 210701, 210801, 210901, 211001),
}


def grant(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for role, rights in GRANTS.items():
            for right in rights:
                cursor.execute(
                    """
                    INSERT INTO "tblRoleRight" ("RoleID", "RightID", "ValidityFrom", "AuditUserId")
                    SELECT DISTINCT r."RoleID", %s, NOW(), 1
                    FROM "tblRole" r
                    WHERE r."ValidityTo" IS NULL AND r."RoleName" ILIKE %s
                      AND NOT EXISTS (
                          SELECT 1 FROM "tblRoleRight" x
                          WHERE x."RoleID" = r."RoleID" AND x."RightID" = %s AND x."ValidityTo" IS NULL
                      )
                    """,
                    [right, f'%{role}', right],
                )


def revoke(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for role, rights in GRANTS.items():
            for right in rights:
                cursor.execute(
                    """
                    DELETE FROM "tblRoleRight"
                    WHERE "RightID" = %s AND "RoleID" IN (
                        SELECT r."RoleID" FROM "tblRole" r
                        WHERE r."ValidityTo" IS NULL AND r."RoleName" ILIKE %s)
                    """,
                    [right, f'%{role}'],
                )


class Migration(migrations.Migration):

    dependencies = [
        ('case_management', '0015_mobile_field_reactivation_right'),
    ]

    operations = [migrations.RunPython(grant, revoke)]
