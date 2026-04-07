"""Add or update a TEE user."""

import os
import getpass

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User

from api.models import UserProfile


class Command(BaseCommand):
    help = 'Add or update a TEE user'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str)
        parser.add_argument('--admin', action='store_true', help='Make user a superuser/staff')
        parser.add_argument('--email', type=str, default='', help='User email address')
        parser.add_argument('--quota', type=int, default=2048, help='Disk quota in MB (default 2048)')

    def handle(self, *args, **options):
        username = options['username']
        is_admin = options['admin']
        email = options['email']
        quota_mb = options['quota']

        # Read password from $PASSWORD env var (for manage.sh), or prompt
        password = os.environ.get('PASSWORD', '')
        if not password:
            password = getpass.getpass(f'Password for {username}: ')
            confirm = getpass.getpass('Confirm password: ')
            if password != confirm:
                raise CommandError('Passwords do not match')

        if len(password) < 4:
            raise CommandError('Password must be at least 4 characters')

        user, created = User.objects.get_or_create(
            username=username,
            defaults={'is_superuser': is_admin, 'is_staff': is_admin, 'email': email},
        )
        if not created:
            user.is_superuser = is_admin
            user.is_staff = is_admin
        if email:
            user.email = email
        user.set_password(password)
        user.save()

        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'quota_mb': quota_mb})
        if not _:
            profile.quota_mb = quota_mb
            profile.save()

        action = 'Created' if created else 'Updated'
        role = 'admin' if is_admin else 'user'
        self.stdout.write(self.style.SUCCESS(f'{action} {role}: {username} (quota={quota_mb} MB)'))
