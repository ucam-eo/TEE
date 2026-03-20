"""Grant or revoke enroller privileges for a TEE user."""

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User

from api.models import UserProfile


class Command(BaseCommand):
    help = 'Grant or revoke enroller privileges for a TEE user'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str)
        parser.add_argument('--revoke', action='store_true',
                            help='Revoke enroller privileges (default: grant)')

    def handle(self, *args, **options):
        username = options['username']
        revoke = options['revoke']

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f'User not found: {username}')

        if user.is_superuser:
            raise CommandError('Admin already has full privileges (no need for enroller flag)')

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.can_enrol = not revoke
        profile.save()

        if revoke:
            self.stdout.write(self.style.SUCCESS(f'Revoked enroller privileges from {username}'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Granted enroller privileges to {username}'))
