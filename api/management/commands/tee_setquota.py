"""Set disk quota for a TEE user."""

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User

from api.models import UserProfile


class Command(BaseCommand):
    help = 'Set disk quota for a TEE user'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str)
        parser.add_argument('quota_mb', type=int, help='Quota in MB')

    def handle(self, *args, **options):
        username = options['username']
        quota_mb = options['quota_mb']

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f'User not found: {username}')

        if user.is_superuser:
            raise CommandError('Admin always has unlimited quota')

        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'quota_mb': quota_mb})
        if not _:
            profile.quota_mb = quota_mb
            profile.save()

        self.stdout.write(self.style.SUCCESS(
            f'Set quota for {username} to {quota_mb} MB ({quota_mb // 1024}G)'
        ))
