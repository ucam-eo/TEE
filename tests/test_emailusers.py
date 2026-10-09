"""tee_emailusers' address cleaning: the BCC list for emailing TEE users."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tee_project.settings")
import django  # noqa: E402

django.setup()

from api.management.commands.tee_emailusers import clean_addresses  # noqa: E402


def test_dedupes_cleans_and_rejects():
    addresses, rejected = clean_addresses([
        "b@x.org",
        "",
        None,
        "  A@y.com ",
        "a@Y.com",           # same address, other case -> one entry
        "c@gmail.com.",      # trailing dot from a sign-up typo
        "not-an-address",
        "b@x.org",
    ])
    assert addresses == ["A@y.com", "b@x.org", "c@gmail.com"]
    assert rejected == ["not-an-address"]
