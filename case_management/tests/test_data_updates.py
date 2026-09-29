from datetime import date
from unittest import mock

from django.test import SimpleTestCase

from case_management.apps import CaseManagementConfig, DEFAULT_DATA_UPDATE_SEVERITY
from case_management.models import Severity
from case_management.services import changed_field_names, data_update_severity


class ChangedFieldNamesTest(SimpleTestCase):

    def test_only_changed_fields(self):
        before = {'first_name': 'Asha', 'last_name': 'Juma', 'dob': '1990-01-01'}
        after = {'id': 'x', 'first_name': 'Aisha', 'last_name': 'Juma', 'dob': date(1990, 1, 1)}
        self.assertEqual(changed_field_names(before, after), ['first_name'])

    def test_blank_and_none_are_equal(self):
        self.assertEqual(changed_field_names({'code': None}, {'code': ''}), [])

    def test_json_ext_keys(self):
        before = {'json_ext': {'national_id': '1', 'phone': 'a'}}
        after = {'json_ext': {'national_id': '2', 'phone': 'a'}}
        self.assertEqual(changed_field_names(before, after), ['json_ext.national_id'])

    def test_mirrored_json_ext_key_counts_once(self):
        before = {'first_name': 'A', 'json_ext': {'first_name': 'A'}}
        after = {'first_name': 'B', 'json_ext': {'first_name': 'B'}}
        self.assertEqual(changed_field_names(before, after), ['first_name'])

    def test_members_compared_as_a_set(self):
        before = {'individuals_data': [{'individual_id': '1'}, {'individual_id': '2'}]}
        same = {'individuals_data': [{'individual_id': '2', 'role': 'HEAD'}, {'individual_id': '1'}]}
        other = {'individuals_data': [{'individual_id': '1'}]}
        self.assertEqual(changed_field_names(before, same), [])
        self.assertEqual(changed_field_names(before, other), ['individuals_data'])


class SeverityTest(SimpleTestCase):

    def setUp(self):
        patcher = mock.patch.object(CaseManagementConfig, 'data_update_severity', DEFAULT_DATA_UPDATE_SEVERITY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_highest_level_wins(self):
        self.assertEqual(data_update_severity(['first_name', 'recipient_type']), Severity.CRITICAL)
        self.assertEqual(data_update_severity(['first_name', 'json_ext.village']), Severity.WARNING)
        self.assertEqual(data_update_severity(['json_ext.village']), Severity.INFO)

    def test_json_ext_key_matches_by_name(self):
        self.assertEqual(data_update_severity(['json_ext.national_id']), Severity.CRITICAL)
