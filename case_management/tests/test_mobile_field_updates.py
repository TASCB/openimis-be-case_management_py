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


class VerificationAttemptNameMatchTest(SimpleTestCase):

    def _schema(self):
        import graphene
        from case_management.gql_queries import VerificationAttemptGQLType

        class Query(graphene.ObjectType):
            attempt = graphene.Field(VerificationAttemptGQLType)

            def resolve_attempt(root, info):
                return info.context

        return graphene.Schema(query=Query)

    def test_returned_name_and_score_are_exposed(self):
        record = SimpleNamespace(
            muse_reference='R1', verification_type='ACCOUNT', result='FAILED', failure_reason='Name mismatch',
            received_at=None, expected_name='Juma Ali Hassan', returned_name='Juma Ally', match_score=62,
            match_details={'reason': 'NAME_MISMATCH', 'member': 'PRIMARY'})
        result = self._schema().execute(
            '{ attempt { expectedName returnedName matchScore matchReason matchMember } }', context_value=record)
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['attempt'], {
            'expectedName': 'Juma Ali Hassan', 'returnedName': 'Juma Ally', 'matchScore': 62,
            'matchReason': 'NAME_MISMATCH', 'matchMember': 'PRIMARY'})

    def test_attempts_before_name_matching_have_no_details(self):
        record = SimpleNamespace(
            muse_reference='R0', verification_type='ACCOUNT', result='PASSED', failure_reason=None, received_at=None,
            expected_name=None, returned_name=None, match_score=None, match_details=None)
        result = self._schema().execute('{ attempt { matchScore matchReason matchMember } }', context_value=record)
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['attempt'], {'matchScore': None, 'matchReason': None, 'matchMember': None})


class SendChangedAccountsTest(SimpleTestCase):

    def _run(self, accounts, apply, batch=50000):
        dispatch = mock.MagicMock(side_effect=lambda ids, after_change: {'success': True, 'count': len(ids), 'batches': 1})
        queryset = mock.MagicMock()
        queryset.values_list.return_value = accounts
        with mock.patch.object(services, 'accounts_awaiting_reverification', return_value=queryset), \
                mock.patch('tasaf_payment.services.MuseVerificationDispatchService') as service, \
                mock.patch('tasaf_payment.apps.TasafPaymentConfig.verification_batch_max_rows', batch):
            service.return_value.dispatch = dispatch
            return services.send_changed_accounts_for_verification('user', apply=apply), dispatch

    def test_dry_run_counts_by_provider_and_sends_nothing(self):
        result, dispatch = self._run([(1, 'CRDB Bank'), (2, 'CRDB Bank'), (3, 'M-PESA')], apply=False)
        self.assertEqual((result['count'], result['by_provider']), (3, {'CRDB Bank': 2, 'M-PESA': 1}))
        dispatch.assert_not_called()

    def test_apply_sends_in_chunks_marked_as_after_a_change(self):
        result, dispatch = self._run([(1, 'A'), (2, 'A'), (3, 'B')], apply=True, batch=2)
        self.assertEqual([c.args[0] for c in dispatch.call_args_list], [[1, 2], [3]])
        self.assertTrue(all(c.kwargs['after_change'] for c in dispatch.call_args_list))
        self.assertEqual((result['sent'], result['batches']), (3, 2))
