"""Derive case management evidence from data predating the module. Idempotent and resumable.
"""
import uuid

from django.core.management.base import BaseCommand
from django.db import transaction

from case_management.apps import MATERIAL_PAYMENT_FIELDS
from case_management.models import (
    Channel, ChangeType, DeactivationMode, HouseholdDeactivation, PaymentChangeAudit,
)

BACKFILL_REASON = 'PRE_MIGRATION'
TRACKED_FIELDS = sorted(MATERIAL_PAYMENT_FIELDS | {'contact_phone', 'is_primary'})


class Command(BaseCommand):
    help = "Derive case management audit and deactivation records from pre-existing data."

    def add_arguments(self, parser):
        parser.add_argument('--chunk-size', type=int, default=5000)
        parser.add_argument('--dry-run', action='store_true',
                            help="Report what would be written; change nothing.")
        parser.add_argument('--only', choices=['payments', 'deactivations'],
                            help="Restrict to one source.")

    def handle(self, *args, **options):
        self.chunk_size = options['chunk_size']
        self.dry_run = options['dry_run']
        only = options.get('only')

        from core.models import User
        self.actor = User.objects.order_by('id').first()
        if not self.actor:
            self.stderr.write("No core.User exists — nothing to attribute the backfill to.")
            return

        if only in (None, 'payments'):
            self._backfill_payment_audits()
        if only in (None, 'deactivations'):
            self._backfill_deactivations()

    # ------------------------------------------------------------------ payments

    def _backfill_payment_audits(self):
        from tasaf_payment.models import PaymentAccount

        history_model = PaymentAccount.history.model
        seen = set(
            PaymentChangeAudit.objects.filter(reason_code=BACKFILL_REASON)
            .values_list('json_ext__backfill_history_id', flat=True)
        )
        account_ids = list(PaymentAccount.objects.values_list('id', flat=True))
        written = skipped = 0

        for start in range(0, len(account_ids), self.chunk_size):
            batch = account_ids[start:start + self.chunk_size]
            rows = list(
                history_model.objects.filter(id__in=batch).order_by('id', 'history_date')
            )
            pending = []
            previous = {}
            for row in rows:
                prior = previous.get(row.id)
                previous[row.id] = row
                if prior is None:
                    continue  # creation is not a change
                diff = self._diff(prior, row)
                if not diff:
                    continue
                key = str(row.history_id)
                if key in seen:
                    skipped += 1
                    continue
                pending.append(self._audit_from(row, diff, key))

            if pending and not self.dry_run:
                with transaction.atomic():
                    for audit in pending:
                        audit.save(user=self.actor)
            written += len(pending)

        verb = "would write" if self.dry_run else "wrote"
        self.stdout.write(self.style.SUCCESS(
            f"payment audit: {verb} {written}, skipped {skipped} already present"))

    @staticmethod
    def _diff(prior, current):
        out = {}
        for field in TRACKED_FIELDS:
            before = getattr(prior, field, None)
            after = getattr(current, field, None)
            if before != after:
                out[field] = {'before': before, 'after': after}
        return out

    def _audit_from(self, row, diff, key):
        material = bool(set(diff) & MATERIAL_PAYMENT_FIELDS)
        if len(diff) > 1:
            change_type = ChangeType.MULTIPLE
        else:
            only_field = next(iter(diff))
            change_type = {
                'account_number': ChangeType.ACCOUNT_NUMBER,
                'fsp_type': ChangeType.PROVIDER,
                'fsp_name': ChangeType.PROVIDER,
                'account_name': ChangeType.ACCOUNT_NAME,
                'contact_phone': ChangeType.CONTACT_PHONE,
                'is_primary': ChangeType.PRIMARY_FLAG,
            }[only_field]
        return PaymentChangeAudit(
            payment_account_id=row.id,
            group_beneficiary_id=row.group_beneficiary_id,
            change_type=change_type,
            is_material=material,
            changed_fields=diff,
            reason_code=BACKFILL_REASON,
            reason_text='Derived from record history; no reason was captured at the time.',
            channel=Channel.SYSTEM,
            previous_verification_status=getattr(row, 'verification_status', None),
            json_ext={'backfill_history_id': key,
                      'source_history_date': row.history_date.isoformat()},
            date_created=row.history_date,
            date_updated=row.history_date,
        )

    # ------------------------------------------------------------- deactivations

    def _backfill_deactivations(self):
        from social_protection.models import GroupBeneficiary

        seen = set(
            HouseholdDeactivation.objects.filter(reason_code=BACKFILL_REASON)
            .values_list('json_ext__backfill_group_beneficiary_id', flat=True)
        )
        # GRADUATED is a programme exit, not a deactivation.
        qs = (GroupBeneficiary.objects
              .filter(status='SUSPENDED', is_deleted=False)
              .values_list('id', 'group_id', 'date_updated'))
        written = skipped = 0
        pending = []

        for gb_id, group_id, updated in qs.iterator(chunk_size=self.chunk_size):
            key = str(gb_id)
            if key in seen:
                skipped += 1
                continue
            pending.append(HouseholdDeactivation(
                group_id=group_id,
                group_beneficiary_id=gb_id,
                mode=DeactivationMode.TEMPORARY,
                reason_code=BACKFILL_REASON,
                reason_text='Suspended before case management; original reason not recorded.',
                effective_date=(updated.date() if updated else None),
                previous_status='ACTIVE',
                json_ext={'backfill_group_beneficiary_id': key},
            ))
            if len(pending) >= self.chunk_size:
                written += self._flush(pending)
                pending = []
        written += self._flush(pending)

        verb = "would write" if self.dry_run else "wrote"
        self.stdout.write(self.style.SUCCESS(
            f"household deactivation: {verb} {written}, skipped {skipped} already present "
            f"(GRADUATED excluded by design)"))

    def _flush(self, pending):
        if not pending:
            return 0
        if not self.dry_run:
            with transaction.atomic():
                for row in pending:
                    row.save(user=self.actor)
        return len(pending)
