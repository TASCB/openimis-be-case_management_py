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
