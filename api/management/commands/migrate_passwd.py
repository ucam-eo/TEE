"""Migrate legacy /data/passwd file to Django auth Users + UserProfiles."""

import logging
from pathlib import Path

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

from api.models import UserProfile
from lib.config import DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_QUOTA_MB = 2048


class Command(BaseCommand):
    help = 'Migrate /data/passwd users to Django auth'

    def add_arguments(self, parser):
        parser.add_argument(
            '--auto',
            action='store_true',
            help='Skip silently if no passwd file or if Users already exist',
        )

    def handle(self, *args, **options):
        passwd_file = DATA_DIR / 'passwd'
        auto = options['auto']

        if not passwd_file.exists():
            if auto:
                return
            self.stderr.write('No passwd file found at %s' % passwd_file)
            return

        if auto and User.objects.exists():
            return  # already migrated

        entries = []
        for line in passwd_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if ':' not in line:
                continue
            parts = line.split(':')
            username = parts[0].strip()
            hashed = parts[1].strip()
            quota_mb = DEFAULT_QUOTA_MB
            if len(parts) > 2 and parts[2].strip():
                try:
                    quota_mb = int(parts[2].strip())
                except ValueError:
                    pass
            if username and hashed:
                entries.append((username, hashed, quota_mb))

        if not entries:
            self.stdout.write('No users found in passwd file')
            return

        created = 0
        for username, hashed, quota_mb in entries:
            if User.objects.filter(username=username).exists():
                self.stdout.write('  Skipping existing user: %s' % username)
                continue

            user = User(username=username)

            # Convert raw bcrypt hash to Django's BCryptPasswordHasher format
            if hashed.startswith('$2b$') or hashed.startswith('$2y$'):
                normalized = hashed.replace('$2y$', '$2b$', 1)
                user.password = 'bcrypt$' + normalized
            elif hashed.startswith('pbkdf2_sha256$'):
                # Already a Django-formatted hash
                user.password = hashed
            else:
                self.stderr.write('  Unknown hash format for %s, skipping' % username)
                continue

            if username == 'admin':
                user.is_superuser = True
                user.is_staff = True
            else:
                user.is_staff = False

            user.save()
            UserProfile.objects.get_or_create(user=user, defaults={'quota_mb': quota_mb})
            created += 1
            self.stdout.write('  Migrated user: %s (quota=%d MB)' % (username, quota_mb))

        # Rename passwd file after successful migration
        migrated_path = passwd_file.with_suffix('.migrated')
        passwd_file.rename(migrated_path)
        self.stdout.write(self.style.SUCCESS(
            'Migrated %d user(s). Renamed passwd -> passwd.migrated' % created
        ))
