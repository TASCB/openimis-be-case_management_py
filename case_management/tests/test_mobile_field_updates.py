from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from case_management import services


class NormalisePaymentFieldsTest(SimpleTestCase):
    def _run(self, account_type, fields, inferred=None, current_name=None):
        account = SimpleNamespace(fsp_type=account_type, fsp_name=current_name)
        with mock.patch.object(services, 'fsp_type_of', return_value=inferred), \
                mock.patch('tasaf_payment.charges.resolve_fsp_code', side_effect=lambda n: n.upper().replace(' ', '')):
            return services.normalise_payment_fields(account, fields)

    def test_another_spelling_of_the_same_provider_is_not_a_change(self):
        out = self._run('MOBILE', {'fsp_name': 'HaloPesa', 'account_number': '0620125469'}, current_name='Halopesa')
        self.assertNotIn('fsp_name', out)
        self.assertEqual(out['account_number'], '255620125469')

    def test_provider_change_takes_the_providers_type(self):
        out = self._run('MOBILE', {'fsp_name': 'CRDB', 'account_number': '0150123456789'}, inferred='BANK')
        self.assertEqual(out['fsp_type'], 'BANK')
        self.assertEqual(out['account_number'], '0150123456789')

    def test_explicit_type_wins(self):
        out = self._run('MOBILE', {'fsp_name': 'CRDB', 'fsp_type': 'MOBILE'}, inferred='BANK')
        self.assertEqual(out['fsp_type'], 'MOBILE')

    def test_same_type_is_not_added(self):
        self.assertNotIn('fsp_type', self._run('MOBILE', {'fsp_name': 'M-PESA'}, inferred='MOBILE'))

    def test_unknown_provider_leaves_the_type(self):
        self.assertNotIn('fsp_type', self._run('MOBILE', {'fsp_name': 'DTB'}, inferred=None))

    def test_mobile_number_is_stored_as_255(self):
        for typed in ('0754111222', '+255 754 111 222', '754111222'):
            self.assertEqual(self._run('MOBILE', {'account_number': typed})['account_number'], '255754111222', typed)

    def test_bank_number_is_not_touched(self):
        self.assertEqual(self._run('BANK', {'account_number': '0754111222'})['account_number'], '0754111222')


class MobileProposalTest(SimpleTestCase):
    def test_only_the_mobile_channel_counts(self):
        self.assertTrue(services.from_mobile('MOBILE'))
        for channel in ('WEB', 'IMPORT', 'SYSTEM', None, ''):
            self.assertFalse(services.from_mobile(channel), channel)

    def test_approved_proposal_is_replayed_through_its_service(self):
        cases = (('update_phone', 'PaymentChangeService', 'update_phone'),
                 ('add_account', 'PaymentChangeService', 'add_account'),
                 ('update_representative', 'HouseholdCaseService', 'update_representative'),
                 ('add_member', 'HouseholdCaseService', 'add_member'),
                 ('reactivate_household', 'HouseholdCaseService', 'reactivate_household'),
                 ('reactivate_member', 'HouseholdCaseService', 'reactivate_member'))
        for action, service, method in cases:
            with mock.patch.object(services, service) as cls:
                getattr(cls.return_value, method).return_value = {'success': True}
                self.assertEqual(services.apply_mobile_proposal('user', action, {'x': 1}), {'success': True})
                getattr(cls.return_value, method).assert_called_once_with(_approved=True, x=1)

    def test_unknown_action_is_a_failure_not_an_error(self):
        self.assertFalse(services.apply_mobile_proposal('user', 'rename_village', {})['success'])

    def _record(self, payload, fields, target=None, is_addition=False):
        return SimpleNamespace(
            summary={'fields': fields}, is_proposal=True, is_addition=is_addition, target=target,
            json_ext={services.MOBILE_PROPOSAL: {'action': 'x', 'payload': payload}})

    def test_proposed_values_against_the_record(self):
        record = self._record({'contact_phone': '255754999000'}, ['contact_phone'],
                              target=SimpleNamespace(contact_phone='255682779443'))
        self.assertEqual(services.pending_changes(record),
                         {'contact_phone': {'before': '255682779443', 'after': '255754999000'}})

    def test_an_addition_has_nothing_before(self):
        record = self._record({'first_name': 'Asha'}, ['first_name'],
                              target=SimpleNamespace(first_name='Household'), is_addition=True)
        self.assertEqual(services.pending_changes(record), {'first_name': {'before': None, 'after': 'Asha'}})
