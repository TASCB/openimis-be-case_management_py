"""Record which channel a pending update came from.

Existing PAYMENT_CHANGE rows take the channel of the latest payment change audit for the same
account written no later than the row. Every other existing row stays NULL: its channel was
never recorded.
"""
from django.db import migrations, models

BACKFILL = """
UPDATE "case_PendingDataUpdate" p
SET channel = (
    SELECT a.channel FROM "case_PaymentChangeAudit" a
    WHERE a.payment_account_id::text = p.object_id
      AND a."DateCreated" <= p."DateCreated"
    ORDER BY a."DateCreated" DESC
    LIMIT 1)
WHERE p.update_type = 'PAYMENT_CHANGE' AND p.channel IS NULL
"""


class Migration(migrations.Migration):

    dependencies = [
        ('case_management', '0008_case_rights_for_field_roles'),
    ]

    operations = [
        migrations.AddField(
            model_name='historicalpendingdataupdate',
            name='channel',
            field=models.CharField(blank=True, choices=[('WEB', 'Web console'), ('MOBILE', 'Mobile app'), ('IMPORT', 'Import / ETL'), ('SYSTEM', 'System')], max_length=20, null=True),
        ),
        migrations.AddField(
            model_name='pendingdataupdate',
            name='channel',
            field=models.CharField(blank=True, choices=[('WEB', 'Web console'), ('MOBILE', 'Mobile app'), ('IMPORT', 'Import / ETL'), ('SYSTEM', 'System')], max_length=20, null=True),
        ),
        migrations.RunSQL(BACKFILL, migrations.RunSQL.noop),
    ]
