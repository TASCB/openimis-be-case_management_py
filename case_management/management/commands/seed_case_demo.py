"""Demo data for the case management UI. Reversible with --undo.

Every row is tagged ``json_ext['_seed'] = 'demo'``, which is what --undo deletes, so it never
touches real records. Audit rows are written directly rather than through PaymentChangeService:
they describe plausible past changes without actually re-pointing a live payment account.
"""
import random
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from case_management.models import (
    Channel, ChangeType, DeactivationMode, FollowUpCategory, FollowUpRemark, FollowUpStatus,
    HouseholdDeactivation, PaymentChangeAudit, PendingDataUpdate, PendingStatus, Priority,
    Severity, UpdateType,
)

SEED = {'_seed': 'case_demo'}

AUDITS = [
    (ChangeType.PROVIDER, True, 'FSP_CHANGED', Channel.WEB,
     {'fsp_name': {'before': 'Airtel Money', 'after': 'NMB Bank'},
      'fsp_type': {'before': 'MOBILE', 'after': 'BANK'}}),
    (ChangeType.ACCOUNT_NUMBER, True, 'ACCOUNT_CLOSED', Channel.WEB,
     {'account_number': {'before': '0123456789', 'after': '0987654321'}}),
    (ChangeType.CONTACT_PHONE, False, None, Channel.MOBILE,
     {'contact_phone': {'before': None, 'after': '+255712345678'}}),
    (ChangeType.ACCOUNT_NAME, True, 'NAME_MISMATCH', Channel.WEB,
     {'account_name': {'before': 'J DOE', 'after': 'JOHN DOE'}}),
    (ChangeType.MULTIPLE, True, 'DATA_CORRECTION', Channel.IMPORT,
     {'account_number': {'before': '111222333', 'after': '444555666'},
      'account_name': {'before': 'A MOSHI', 'after': 'AMINA MOSHI'}}),
]

REMARKS = [
    (FollowUpCategory.PAYMENT_FAILURE, FollowUpStatus.OPEN, Priority.HIGH, -4,
     'Transfer bounced at the FSP; awaiting confirmation of the new account number.'),
    (FollowUpCategory.ACCOUNT_VERIFICATION, FollowUpStatus.IN_PROGRESS, Priority.NORMAL, 3,
     'MUSE returned a name mismatch. Ward officer visiting to confirm the ID.'),
    (FollowUpCategory.DATA_QUALITY, FollowUpStatus.ESCALATED, Priority.URGENT, -9,
     'Two households registered against the same national ID. Escalated to the council.'),
    (FollowUpCategory.BENEFICIARY_CONTACT, FollowUpStatus.RESOLVED, Priority.LOW, -20,
     'Beneficiary phone unreachable for three cycles; new number captured at the pay point.'),
    (FollowUpCategory.OTHER, FollowUpStatus.OPEN, Priority.NORMAL, 6,
     'Household asked about the payment schedule for the next cycle.'),
]


class Command(BaseCommand):
    help = "Seed (or remove) demo case management records for the UI."

    def add_arguments(self, parser):
        parser.add_argument('--households', type=int, default=8)
        parser.add_argument('--group', type=str,
                            help='Seed one household by code or UUID, creating a beneficiary and '
                                 'payment account for it if it has none.')
        parser.add_argument('--undo', action='store_true')

    def handle(self, *args, **options):
        from core.models import User
        self.user = User.objects.filter(username='Admin').first() or User.objects.order_by('id').first()
        if not self.user:
            self.stderr.write('No core.User to attribute the demo data to.')
            return
        if options['undo']:
            return self._undo()
        self._seed(options['households'], options.get('group'))

    # ------------------------------------------------------------------ seed

    def _seed(self, wanted, group_ref=None):
        from social_protection.models import GroupBeneficiary
        from tasaf_payment.models import PaymentAccount

        if group_ref:
            accounts = [self._ensure_account(self._resolve_group(group_ref))]
        else:
            accounts = list(PaymentAccount.objects
                            .filter(is_deleted=False, is_primary=True,
                                    group_beneficiary__isnull=False)
                            .select_related('group_beneficiary__group')[:wanted])
            if len(accounts) < wanted:
                spare = list(GroupBeneficiary.objects
                             .filter(is_deleted=False)
                             .exclude(payment_accounts__is_deleted=False)
                             .select_related('group')[:wanted - len(accounts)])
                accounts += [self._make_account(b) for b in spare]
        accounts = [a for a in accounts if a]
        if not accounts:
            self.stderr.write('No payment accounts to attach demo data to.')
            return

        rng = random.Random(42)
        counts = {'audits': 0, 'remarks': 0, 'deactivations': 0, 'pending': 0}

        with transaction.atomic():
            for idx, account in enumerate(accounts):
                beneficiary = account.group_beneficiary
                group = beneficiary.group

                for n, (ctype, material, reason, channel, fields) in enumerate(
                        rng.sample(AUDITS, rng.randint(2, 4))):
                    audit = PaymentChangeAudit(
                        payment_account=account, group_beneficiary=beneficiary,
                        change_type=ctype, is_material=material, changed_fields=fields,
                        reason_code=reason,
                        reason_text=('Recorded during a field visit.' if reason else None),
                        channel=channel,
                        previous_verification_status=1 if material else None,
                        previous_pre_audit_status='PASSED' if material else None,
                        json_ext=dict(SEED))
                    audit.save(user=self.user)
                    audit.date_created = timezone.now() - timedelta(days=(n + 1) * 9 + idx)
                    audit.save(user=self.user)
                    counts['audits'] += 1

                for cat, status, prio, due_offset, text in rng.sample(REMARKS, rng.randint(1, 3)):
                    remark = FollowUpRemark(
                        group=group, category=cat, remark=text, status=status, priority=prio,
                        assigned_to=self.user, due_date=date.today() + timedelta(days=due_offset),
                        resolved_at=(timezone.now() if status == FollowUpStatus.RESOLVED else None),
                        resolution_note=('Confirmed with the beneficiary.'
                                         if status == FollowUpStatus.RESOLVED else None),
                        escalated_at=(timezone.now()
                                      if status == FollowUpStatus.ESCALATED else None),
                        json_ext=dict(SEED))
                    remark.save(user=self.user)
                    counts['remarks'] += 1

                past = HouseholdDeactivation(
                    group=group, group_beneficiary=beneficiary,
                    mode=DeactivationMode.TEMPORARY, reason_code='REGISTRATION_ERROR',
                    reason_text='Deactivated in error during the 2026 clean-up.',
                    effective_date=date.today() - timedelta(days=120 + idx),
                    previous_status='ACTIVE',
                    reactivated_at=timezone.now() - timedelta(days=100 + idx),
                    reactivated_by=self.user,
                    reactivation_reason='Error confirmed; household reinstated.',
                    json_ext=dict(SEED))
                past.save(user=self.user)
                counts['deactivations'] += 1

                # first household: currently deactivated, so the Reactivate button is visible
                if idx == 0:
                    record = HouseholdDeactivation(
                        group=group, group_beneficiary=beneficiary,
                        mode=DeactivationMode.TEMPORARY, reason_code='RELOCATED',
                        reason_text='Household away for the harvest season; confirmed by the ward.',
                        effective_date=date.today() - timedelta(days=12),
                        previous_status=beneficiary.status, json_ext=dict(SEED))
                    record.save(user=self.user)
                    GroupBeneficiary.objects.filter(pk=beneficiary.pk).update(status='SUSPENDED')
                    counts['deactivations'] += 1

                if idx < 4:
                    counts['pending'] += self._pending(account, group)

        self.stdout.write(self.style.SUCCESS(
            f"seeded across {len(accounts)} households: {counts['audits']} audits, "
            f"{counts['remarks']} follow-ups, {counts['deactivations']} deactivations, "
            f"{counts['pending']} pending updates"))
        self.stdout.write('')
        self.stdout.write('open these in Registry -> Targeted Households -> Case Management:')
        for idx, account in enumerate(accounts):
            group = account.group_beneficiary.group
            flag = '  (currently DEACTIVATED)' if idx == 0 else ''
            self.stdout.write(f'  {group.code}  /front/groups/group/{group.id}{flag}')
        self.stdout.write('')
        self.stdout.write('remove with: manage.py seed_case_demo --undo')

    def _resolve_group(self, ref):
        from individual.models import Group
        group = (Group.objects.filter(code=ref, is_deleted=False).first()
                 or Group.objects.filter(id=ref).first())
        if not group:
            raise CommandError(f'No household matching {ref!r}')
        return group

    def _ensure_account(self, group):
        """Give one household everything the Case Management tab needs."""
        from social_protection.models import BenefitPlan, GroupBeneficiary
        from tasaf_payment.models import PaymentAccount

        account = (PaymentAccount.objects
                   .filter(group_beneficiary__group=group, is_deleted=False, is_primary=True)
                   .select_related('group_beneficiary__group').first())
        if account:
            return account
        beneficiary = GroupBeneficiary.objects.filter(group=group, is_deleted=False).first()
        if not beneficiary:
            plan = BenefitPlan.objects.filter(is_deleted=False, type='GROUP').first()
            if not plan:
                raise CommandError('No GROUP benefit plan to enrol the household into.')
            beneficiary = GroupBeneficiary(
                group=group, benefit_plan=plan, status='ACTIVE', json_ext=dict(SEED))
            beneficiary.save(user=self.user)
            self.stdout.write(f'  created a demo GroupBeneficiary for {group.code}')
        return self._make_account(beneficiary)

    def _make_account(self, beneficiary):
        from tasaf_payment.models import PaymentAccount

        account = PaymentAccount(
            group_beneficiary=beneficiary,
            account_number=f'0{random.Random(str(beneficiary.id)).randint(700000000, 799999999)}',
            account_name='DEMO ACCOUNT',
            fsp_type='MOBILE', fsp_name='Airtel Money',
            verification_status=1, pre_audit_status='PASSED',
            is_primary=True, json_ext=dict(SEED))
        account.save(user=self.user)
        return account

    def _pending(self, account, group):
        from django.contrib.contenttypes.models import ContentType

        content_type = ContentType.objects.get_for_model(account.__class__)
        # case_pdu_one_pending_per_target forbids a second PENDING row for the same target
        if PendingDataUpdate.objects.filter(
                content_type=content_type, object_id=str(account.pk),
                status=PendingStatus.PENDING, is_deleted=False).exists():
            return 0
        record = PendingDataUpdate(
            content_type=content_type,
            object_id=str(account.pk), group=group, location=group.location,
            update_type=UpdateType.PAYMENT_CHANGE, status=PendingStatus.PENDING,
            severity=Severity.CRITICAL,
            summary={'change_type': 'ACCOUNT_NUMBER', 'fields': ['account_number'],
                     'reason_code': 'ACCOUNT_CLOSED'},
            submitted_by=self.user, json_ext=dict(SEED))
        record.save(user=self.user)
        return 1

    # ------------------------------------------------------------------ undo

    def _undo(self):
        from social_protection.models import GroupBeneficiary

        with transaction.atomic():
            # restore any beneficiary this seed suspended, before deleting the record
            restored = 0
            for record in HouseholdDeactivation.objects.filter(
                    json_ext___seed='case_demo', reactivated_at__isnull=True,
                    group_beneficiary__isnull=False):
                GroupBeneficiary.objects.filter(pk=record.group_beneficiary_id).update(
                    status=record.previous_status or 'ACTIVE')
                restored += 1
            from tasaf_payment.models import PaymentAccount
            counts = {
                'audits': PaymentChangeAudit.objects.filter(json_ext___seed='case_demo').delete()[0],
                'remarks': FollowUpRemark.objects.filter(json_ext___seed='case_demo').delete()[0],
                'deactivations': HouseholdDeactivation.objects.filter(
                    json_ext___seed='case_demo').delete()[0],
                'pending': PendingDataUpdate.objects.filter(json_ext___seed='case_demo').delete()[0],
                # only accounts this seeder created; never anything it merely borrowed
                'accounts': PaymentAccount.objects.filter(
                    json_ext___seed='case_demo', account_name='DEMO ACCOUNT').delete()[0],
                'beneficiaries': GroupBeneficiary.objects.filter(
                    json_ext___seed='case_demo').delete()[0],
            }
        self.stdout.write(self.style.SUCCESS(
            f"removed {counts}; beneficiaries restored: {restored}"))
