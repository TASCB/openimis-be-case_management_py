from unittest import mock

from django.test import SimpleTestCase, TestCase

from case_management import validation as v

TABLE = {'HALOPESA': ['061', '062'], 'AIRTELMONEY': ['068', '069', '078']}
CODES = {'Halopesa': 'HALOPESA', 'Airtel Money': 'AIRTELMONEY'}


def _patched(test):
    for target, kwargs in (('tasaf_payment.prefixes.table', {'return_value': TABLE}),
                           ('tasaf_payment.charges.resolve_fsp_code', {'side_effect': CODES.get})):
        p = mock.patch(target, **kwargs)
        p.start()
        test.addCleanup(p.stop)


class ValidateMobileProviderTest(SimpleTestCase):
    def setUp(self):
        _patched(self)

    def test_a_number_of_another_provider_is_refused(self):
        with self.assertRaises(v.CaseValidationError) as caught:
            v.validate_mobile_provider('MOBILE', 'Halopesa', '0782191101')
        self.assertEqual(caught.exception.code, v.CM_PROVIDER_MISMATCH)
        self.assertIn('078 is a AIRTELMONEY number', str(caught.exception))

    def test_own_unknown_and_bank_numbers_pass(self):
        v.validate_mobile_provider('MOBILE', 'Halopesa', '255612191101')
        v.validate_mobile_provider('MOBILE', 'Halopesa', '255632191101')
        v.validate_mobile_provider('BANK', 'NMB', '0150123456789')


class ProviderMismatchListTest(TestCase):
    def setUp(self):
        _patched(self)
        from tasaf_payment.models import PaymentAccount
        self.accounts = list(PaymentAccount.objects.filter(is_deleted=False).order_by('id')[:3])
        if len(self.accounts) < 3:
            self.skipTest('needs three payment accounts')
        for account, (name, number) in zip(self.accounts, (('Halopesa', '255782191101'),
                                                            ('Halopesa', '255612191101'),
                                                            ('Airtel Money', '255782191102'))):
            PaymentAccount.objects.filter(id=account.id).update(fsp_name=name, fsp_type='MOBILE',
                                                                account_number=number)

    def test_only_the_mismatched_account_is_listed(self):
        from case_management.schema import provider_mismatch_q
        from tasaf_payment.models import PaymentAccount
        with mock.patch('tasaf_payment.reports.fsp_names_for_code',
                        side_effect=lambda code: [n for n, c in CODES.items() if c == code]):
            ids = set(PaymentAccount.objects.filter(provider_mismatch_q(), id__in=[a.id for a in self.accounts])
                      .values_list('id', flat=True))
        self.assertEqual(ids, {self.accounts[0].id})


class WithdrawnListTest(TestCase):
    def setUp(self):
        from core.models import User
        from tasaf_payment.models import PaymentAccount, VerificationStatus
        self.user = User.objects.filter(username='Admin').first()
        self.accounts = list(PaymentAccount.objects.filter(is_deleted=False).order_by('id')[:3])
        if self.user is None or len(self.accounts) < 3:
            self.skipTest('needs the Admin user and three payment accounts')
        PaymentAccount.objects.filter(id__in=[a.id for a in self.accounts]).update(
            verification_status=VerificationStatus.FAILED, json_ext={})

    def _fail(self, account, withdrawn, minutes_ago):
        from datetime import timedelta
        from django.utils import timezone
        from tasaf_payment.models import MuseVerificationRecord, PaymentAccount
        if withdrawn:
            PaymentAccount.objects.filter(id=account.id).update(
                json_ext={'verification_withdrawn': {'code': 'PROVIDER_MISMATCH'}})
        record = MuseVerificationRecord(payment_account=account, result='FAILED', failure_reason='x')
        record.save(username=self.user.username)
        MuseVerificationRecord.objects.filter(id=record.id).update(
            received_at=timezone.now() - timedelta(minutes=minutes_ago))

    def _list(self, **kwargs):
        from case_management.schema import Query
        info = mock.Mock(context=mock.Mock(user=self.user))
        with mock.patch('case_management.schema._check'), \
                mock.patch('case_management.schema._scope_to_user', side_effect=lambda qs, *a: qs), \
                mock.patch('case_management.schema.gql_optimizer.query', side_effect=lambda qs, info: qs):
            qs = Query().resolve_account_correction(info, **kwargs)
        return [a.id for a in qs if a.id in {x.id for x in self.accounts}]

    def test_newest_failure_first_and_the_withdrawn_reason(self):
        old, new, sent = self.accounts
        self._fail(old, True, 60)
        self._fail(new, True, 1)
        self._fail(sent, False, 30)
        self.assertEqual(self._list(), [new.id, sent.id, old.id])
        self.assertEqual(self._list(reason='WITHDRAWN'), [new.id, old.id])


class PendingChangeShownTest(TestCase):
    def setUp(self):
        from core.models import User
        from tasaf_payment.models import PaymentAccount
        self.user = User.objects.filter(username='Admin').first()
        self.account = PaymentAccount.objects.filter(is_deleted=False, group_beneficiary__isnull=False).first()
        if self.user is None or self.account is None:
            self.skipTest('needs the Admin user and a payment account with a household')
        PaymentAccount.objects.filter(id=self.account.id).update(
            fsp_name='Vodacom M-Pesa', fsp_type='MOBILE', account_number='255751000001')
        for target in ('case_management.services.require_in_scope',
                       'tasaf_payment.services.account_payment_in_flight'):
            p = mock.patch(target, return_value=False)
            p.start()
            self.addCleanup(p.stop)

    def test_a_queued_number_change_is_reported_on_the_account(self):
        from case_management.gql_queries import PaymentAccountCorrectionGQLType as T
        from case_management.services import PaymentChangeService
        from tasaf_payment.models import PaymentAccount
        result = PaymentChangeService(self.user).update_details(
            str(self.account.id), {'account_number': '0751017945'},
            reason_code='DATA_CORRECTION', reason_text='Number corrected after a GePG withdrawal')
        self.assertTrue(result.get('success'), result)
        self.assertTrue((result.get('data') or {}).get('queued'), result)
        account = PaymentAccount.objects.get(id=self.account.id)
        self.assertEqual(account.account_number, '255751000001')
        change = T.resolve_pending_change(account, None)
        self.assertEqual(change.fields, {'account_number': '255751017945'})
        self.assertEqual(change.previous, {'account_number': '255751000001'})
        self.assertEqual(change.submitted_by, 'Admin')
        self.assertTrue(T.resolve_waiting_for_approval(account, None))
