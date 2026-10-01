"""Demo Pending updates for every case, made through the real services on `full_demo` households
only. Everything created is tagged ``json_ext['_seed'] = 'pending_demo'``; --undo removes it."""
import random

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from case_management.apps import CaseManagementConfig
from case_management.models import (
    Channel, FollowUpRemark, PaymentChangeAudit, PendingDataUpdate, PendingStatus,
)

SEED = 'pending_demo'
MAKER = 'Admin'
DECIDER = 'tz_tmo'


def _msisdn(rng):
    return f'2557{rng.randint(10_000_000, 99_999_999)}'


class Command(BaseCommand):
    help = "Seed (or remove with --undo) demo Pending updates for every case."

    def add_arguments(self, parser):
        parser.add_argument('--undo', action='store_true')
        parser.add_argument('--keep-legacy', action='store_true',
                            help="Keep the old case_demo pending rows (they sit on real accounts).")

    def handle(self, *args, **options):
        from core.models import User
        self.maker = User.objects.filter(username=MAKER, validity_to__isnull=True).first()
        self.decider = User.objects.filter(username=DECIDER, validity_to__isnull=True).first()
        if not self.maker or not self.decider:
            raise CommandError(f'Needs users {MAKER} (maker) and {DECIDER} (decider)')
        if options['undo']:
            return self._undo()
        if not options['keep_legacy']:
            removed = PendingDataUpdate.objects.filter(json_ext___seed='case_demo').delete()[0]
            self.stdout.write(f'removed {removed} legacy case_demo pending rows')
        self._seed()

    # ------------------------------------------------------------------ helpers

    def _accounts(self, fsp_type, n):
        from django.contrib.contenttypes.models import ContentType
        from tasaf_payment.models import PaymentAccount

        busy = PendingDataUpdate.objects.filter(
            content_type=ContentType.objects.get_for_model(PaymentAccount),
            status=PendingStatus.PENDING, is_deleted=False).values_list('object_id', flat=True)
        qs = (PaymentAccount.objects
              .filter(is_deleted=False, is_primary=True, json_ext___seed='full_demo', fsp_type=fsp_type,
                      group_beneficiary__isnull=False)
              .exclude(id__in=[str(i) for i in busy]).order_by('id'))
        found = list(qs[:n])
        if len(found) < n:
            raise CommandError(f'Not enough full_demo {fsp_type} accounts; run seed_full_demo first')
        return found

    def _households(self, n):
        from individual.models import Group
        groups = list(Group.objects.filter(is_deleted=False, json_ext___seed='full_demo')
                      .order_by('id')[50:50 + n])
        if len(groups) < n:
            raise CommandError('Not enough full_demo households; run seed_full_demo first')
        return groups

    def _member(self, group):
        from individual.models import GroupIndividual
        return (GroupIndividual.objects.filter(group=group, is_deleted=False)
                .select_related('individual').order_by('id').first())

    def _ok(self, label, result):
        if isinstance(result, dict) and not result.get('success', True):
            raise CommandError(f'{label}: {result.get("detail") or result.get("message")}')
        self.stdout.write(f'  {label}')
        return result

    def _latest_for(self, target):
        from django.contrib.contenttypes.models import ContentType
        return (PendingDataUpdate.objects
                .filter(content_type=ContentType.objects.get_for_model(target.__class__),
                        object_id=str(target.pk), is_deleted=False)
                .order_by('-date_created').first())

    def _decide(self, target, approve, note):
        from case_management.services import PendingUpdateService
        record = self._latest_for(target)
        self._ok(f'decide {"approve" if approve else "reject"} {record.update_type}',
                 PendingUpdateService(self.decider).decide(str(record.id), approve, note))

    def _set_channel(self, target, channel):
        PendingDataUpdate.objects.filter(id=self._latest_for(target).id).update(channel=channel)

    # ------------------------------------------------------------------ seed

    def _seed(self):
        from individual.services import GroupService, IndividualService
        from case_management.services import (
            DataUpdateService, HouseholdCaseService, PaymentChangeService, snapshot,
        )
        from location.models import Location

        rng = random.Random(7)
        before = {
            'pending': set(PendingDataUpdate.objects.values_list('id', flat=True)),
            'audits': set(PaymentChangeAudit.objects.values_list('id', flat=True)),
            'remarks': set(FollowUpRemark.objects.values_list('id', flat=True)),
        }
        from tasks_management.models import Task
        before['tasks'] = set(Task.objects.values_list('id', flat=True))

        mobile = self._accounts('MOBILE', 8)
        bank = self._accounts('BANK', 2)
        groups = self._households(7)
        pay = PaymentChangeService(self.maker)

        with transaction.atomic():
            self.stdout.write('payment changes (proposals, need approval):')
            a = mobile[0]
            self._ok('number change, web — PENDING', pay.update_details(
                a.id, {'account_number': _msisdn(rng)}, reason_code='ACCOUNT_INVALID',
                version=a.version, channel=Channel.WEB))
            a = mobile[1]
            self._ok('provider change, mobile — PENDING', pay.update_details(
                a.id, {'fsp_name': 'Halopesa' if a.fsp_name != 'Halopesa' else 'Airtel Money'},
                reason_code='FSP_CHANGED', version=a.version, channel=Channel.MOBILE))
            a = mobile[2]
            self._ok('account name change', pay.update_details(
                a.id, {'account_name': f'{a.account_name} (corrected)'}, reason_code='NAME_MISMATCH',
                version=a.version, channel=Channel.WEB))
            self._decide(a, True, 'Name confirmed against the national ID.')
            a = bank[0]
            self._ok('bank account change', pay.update_details(
                a.id, {'account_number': str(rng.randint(10**11, 10**12 - 1))}, reason_code='ACCOUNT_CLOSED',
                version=a.version, channel=Channel.MOBILE))
            self._decide(a, False, 'Bank letter does not match the beneficiary; resubmit with proof.')

            self.stdout.write('payment change applied without prior approval (review after the fact):')
            a = bank[1]
            saved = CaseManagementConfig.payment_change_requires_approval
            CaseManagementConfig.payment_change_requires_approval = False
            try:
                self._ok('bank account change via import — PENDING review', pay.update_details(
                    a.id, {'account_number': str(rng.randint(10**11, 10**12 - 1))},
                    reason_code='DATA_CORRECTION', version=a.version, channel=Channel.IMPORT))
            finally:
                CaseManagementConfig.payment_change_requires_approval = saved

            self.stdout.write('contact phone (info, applied):')
            a = mobile[3]
            self._ok('phone, mobile — PENDING', pay.update_phone(a.id, _msisdn(rng), version=a.version,
                                                                  channel=Channel.MOBILE))
            a = mobile[4]
            self._ok('phone, web', pay.update_phone(a.id, _msisdn(rng), version=a.version,
                                                    channel=Channel.WEB))
            self._decide(a, True, 'Acknowledged.')
            a = mobile[5]
            self._ok('phone, first edit (will be superseded)', pay.update_phone(
                a.id, _msisdn(rng), version=a.version, channel=Channel.MOBILE))
            a.refresh_from_db()
            self._ok('phone, second edit — supersedes the first', pay.update_phone(
                a.id, _msisdn(rng), version=a.version, channel=Channel.MOBILE))

            self.stdout.write('representative change (warning, applied):')
            from individual.models import GroupIndividual
            membership = (GroupIndividual.objects.filter(group=groups[0], is_deleted=False)
                          .exclude(recipient_type='PRIMARY').order_by('id').first())
            self._ok('recipient made PRIMARY — PENDING', HouseholdCaseService(self.maker).update_representative(
                membership.id, recipient_type='PRIMARY', reason_text='Head is working away; spouse collects.'))

            self.stdout.write('member and household edits (proposals through their tasks):')
            people = IndividualService(self.maker)
            m = self._member(groups[1]).individual
            self._ok('member name correction, mobile — PENDING', people.create_update_task({
                'id': str(m.id), 'first_name': f'{m.first_name}a', 'last_name': m.last_name,
                'dob': m.dob.isoformat(), 'json_ext': m.json_ext}))
            self._set_channel(m, Channel.MOBILE)
            m = self._member(groups[2]).individual
            self._ok('member date of birth', people.create_update_task({
                'id': str(m.id), 'first_name': m.first_name, 'last_name': m.last_name,
                'dob': m.dob.replace(year=m.dob.year - 1).isoformat(), 'json_ext': m.json_ext}))
            self._decide(m, True, 'Birth certificate seen.')
            m = self._member(groups[3]).individual
            self._ok('member surname', people.create_update_task({
                'id': str(m.id), 'first_name': m.first_name, 'last_name': f'{m.last_name}i',
                'dob': m.dob.isoformat(), 'json_ext': m.json_ext}))
            self._decide(m, False, 'No document supports the new surname.')
            m = self._member(groups[4]).individual
            try:
                with transaction.atomic():
                    self._ok('member edit that will not save (invalid date)', people.create_update_task({
                        'id': str(m.id), 'first_name': m.first_name, 'last_name': m.last_name,
                        'dob': '1990-02-30', 'json_ext': m.json_ext}))
                    self._decide(m, True, 'Approved; the save fails, so the row shows "not applied".')
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f'  skipped the "not applied" case: {exc}'))

            g = groups[5]
            new_village = (Location.objects.filter(type='V', validity_to__isnull=True)
                           .exclude(id=g.location_id).order_by('id').first())
            self._ok('household location change — PENDING', GroupService(self.maker).create_update_task({
                'id': str(g.id), 'code': g.code, 'location_id': new_village.id}))

            self.stdout.write('edits saved directly, reviewed after the fact:')
            m = self._member(groups[6]).individual
            snap = snapshot(m)
            m.first_name = f'{m.first_name}e'
            m.save(username=self.maker.username)
            DataUpdateService(self.maker).record_applied(m, snap)
            self._set_channel(m, Channel.SYSTEM)
            self._ok('member name fixed by a system job — PENDING review', {'success': True})
            g = groups[0]
            snap = snapshot(g)
            g.code = f'{g.code}-R'
            g.save(username=self.maker.username)
            if DataUpdateService(self.maker).record_applied(g, snap):
                self._decide(g, False, 'Code change not expected; flag for data quality.')

            self._tag(before)

        self._report()

    def _tag(self, before):
        from tasks_management.models import Task

        for model, key in ((PendingDataUpdate, 'pending'), (PaymentChangeAudit, 'audits'),
                           (FollowUpRemark, 'remarks'), (Task, 'tasks')):
            for obj in model.objects.exclude(id__in=before[key]):
                obj.json_ext = {**(obj.json_ext or {}), '_seed': SEED}
                model.objects.filter(id=obj.id).update(json_ext=obj.json_ext)

    def _report(self):
        from django.db.models import Count
        rows = (PendingDataUpdate.objects.filter(json_ext___seed=SEED)
                .values('update_type', 'is_proposal', 'status', 'severity', 'channel')
                .annotate(n=Count('id')).order_by('update_type', '-is_proposal', 'status'))
        self.stdout.write(self.style.SUCCESS('pending_demo rows:'))
        for r in rows:
            kind = 'proposal' if r['is_proposal'] else 'applied'
            self.stdout.write(f"  {r['update_type']:<22} {kind:<9} {r['status']:<11} "
                              f"{r['severity']:<9} {r['channel'] or '-':<7} x{r['n']}")
        failed = PendingDataUpdate.objects.filter(json_ext___seed=SEED, summary__apply_failed=True).count()
        self.stdout.write(f'  approved but not applied: {failed}')

    # ------------------------------------------------------------------ undo

    def _undo(self):
        from tasks_management.models import Task
        with transaction.atomic():
            counts = {
                'pending': PendingDataUpdate.objects.filter(json_ext___seed=SEED).delete()[0],
                'audits': PaymentChangeAudit.objects.filter(json_ext___seed=SEED).delete()[0],
                'remarks': FollowUpRemark.objects.filter(json_ext___seed=SEED).delete()[0],
                'tasks': Task.objects.filter(json_ext___seed=SEED).delete()[0],
            }
        self.stdout.write(self.style.SUCCESS(
            f'removed {counts}. Changes already applied to full_demo households stay (synthetic data).'))
