from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = ("Send payment accounts left PENDING by an approved change for verification. "
            "Dry run unless --apply. Meant to run daily (cron).")

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--user', help='Username the dispatch is recorded under; default the inbound system user.')

    def handle(self, *args, **options):
        from core.models import User
        from tasaf_payment.apps import TasafPaymentConfig
        from case_management.services import send_changed_accounts_for_verification

        username = options.get('user') or TasafPaymentConfig.inbound_system_username or 'Admin'
        user = User.objects.filter(username=username).first()
        if user is None:
            raise CommandError(f'No user {username!r}')

        result = send_changed_accounts_for_verification(user, apply=options['apply'])
        if not result['count']:
            self.stdout.write('No changed account is waiting for verification.')
            return
        self.stdout.write(self.style.MIGRATE_HEADING(f"{result['count']} changed account(s) waiting for verification"))
        for provider, n in sorted(result['by_provider'].items(), key=lambda kv: (kv[0] or '')):
            self.stdout.write(f'  {provider or "-":<24} {n}')
        if not options['apply']:
            self.stdout.write(self.style.WARNING('Dry run: nothing was sent. Add --apply to send them.'))
            return
        self.stdout.write(self.style.SUCCESS(f"Sent {result['sent']} in {result['batches']} message(s)."))
        for error in result['errors']:
            self.stderr.write(self.style.ERROR(error))
