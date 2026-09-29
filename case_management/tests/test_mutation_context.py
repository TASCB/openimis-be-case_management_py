from django.test import SimpleTestCase

from case_management import mutation_context as mc
from case_management.models import Channel
from case_management.services import resolve_channel


class ChannelFromExtensionsTest(SimpleTestCase):

    def test_dict_and_json_string(self):
        self.assertEqual(mc.channel_from_extensions({'channel': 'MOBILE'}), Channel.MOBILE)
        self.assertEqual(mc.channel_from_extensions('{"channel": "mobile"}'), Channel.MOBILE)

    def test_absent_or_invalid_is_none(self):
        for value in (None, {}, '', 'not json', {'channel': ''}, {'channel': 'FAX'}, ['MOBILE']):
            self.assertIsNone(mc.channel_from_extensions(value), value)


class OnMutationTest(SimpleTestCase):

    def tearDown(self):
        mc.clear()

    def test_returns_empty_error_list(self):
        self.assertEqual(mc.on_mutation(None, data={'mutation_extensions': {'channel': 'MOBILE'}}), [])
        self.assertEqual(mc.on_mutation(None, data=None), [])

    def test_every_mutation_overwrites_the_previous_channel(self):
        mc.on_mutation(None, mutation_class='A', data={'mutation_extensions': {'channel': 'MOBILE'}})
        self.assertEqual(resolve_channel(), Channel.MOBILE)
        mc.on_mutation(None, mutation_class='B', data={})
        self.assertEqual(resolve_channel(), Channel.WEB)

    def test_explicit_argument_wins(self):
        mc.on_mutation(None, data={'mutation_extensions': {'channel': 'MOBILE'}})
        self.assertEqual(resolve_channel(Channel.IMPORT), Channel.IMPORT)

    def test_clear(self):
        mc.on_mutation(None, data={'mutation_extensions': {'channel': 'MOBILE'}})
        mc.clear()
        self.assertIsNone(mc.current())
        self.assertEqual(resolve_channel(), Channel.WEB)
