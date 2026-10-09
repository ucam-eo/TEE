"""Print the email addresses of active TEE users, ready to paste into BCC.

Email is optional at sign-up, so not every user has one. Addresses are
deduplicated case-insensitively (several people have more than one account)
and lightly cleaned (stray whitespace, a trailing dot); anything that still
doesn't look like an address is reported rather than included.

TEE has no outgoing mail configured, so this only lists addresses -- send
the message from your own mail client, with everyone in BCC.
"""

import re

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

_ADDRESS = re.compile(r'^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$')


def clean_addresses(emails):
    """-> (addresses, rejected): unique cleaned addresses sorted
    case-insensitively (first spelling kept), and inputs that aren't addresses."""
    unique, rejected = {}, []
    for raw in emails:
        email = (raw or '').strip().rstrip('.')
        if not email:
            continue
        if not _ADDRESS.match(email):
            rejected.append(raw)
            continue
        unique.setdefault(email.lower(), email)
    return sorted(unique.values(), key=str.lower), rejected


class Command(BaseCommand):
    help = 'Print the email addresses of active TEE users, ready to paste into BCC'

    def handle(self, *args, **options):
        users = User.objects.filter(is_active=True)
        n_users = users.count()
        emails = list(users.values_list('email', flat=True))
        addresses, rejected = clean_addresses(emails)
        n_without = sum(1 for e in emails if not (e or '').strip())

        self.stdout.write('')
        self.stdout.write(
            f'  {len(addresses)} addresses from {n_users} active users '
            f'({n_without} without an email)'
        )
        for raw in rejected:
            self.stdout.write(f'  Skipped, not an address: {raw!r}')
        self.stdout.write('')
        self.stdout.write(', '.join(addresses))
        self.stdout.write('')
