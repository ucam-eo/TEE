"""List all TEE users."""

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User


class Command(BaseCommand):
    help = 'List all TEE users'

    def handle(self, *args, **options):
        users = User.objects.all().order_by('username')
        if not users.exists():
            self.stdout.write('No users configured (auth disabled).')
            return

        self.stdout.write('')
        self.stdout.write('  %-20s %-10s %s' % ('USER', 'ROLE', 'QUOTA'))
        self.stdout.write('  %-20s %-10s %s' % ('----', '----', '-----'))
        for user in users:
            if user.is_superuser:
                role = 'admin'
            else:
                try:
                    role = 'enroller' if user.profile.can_enrol else 'user'
                except Exception:
                    role = 'user'
            if user.is_superuser:
                quota_str = 'unlimited'
            else:
                try:
                    quota_mb = user.profile.quota_mb
                    quota_str = f'{quota_mb // 1024}G ({quota_mb} MB)'
                except Exception:
                    quota_str = 'default (2048 MB)'
            self.stdout.write('  %-20s %-10s %s' % (user.username, role, quota_str))
        self.stdout.write('')
