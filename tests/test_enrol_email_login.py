"""Enrolment accepts an email address as the login (scripts/tee-adduser.sh)."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tee_project.settings")
import django  # noqa: E402

django.setup()

import pytest  # noqa: E402

from api.views.enrolment import _email_login  # noqa: E402


def test_plain_usernames_are_not_email_logins():
    assert _email_login("kmueller") is None


def test_email_login_is_lower_cased():
    assert _email_login("Konstantin.Mueller@Uni-Wuerzburg.de") == "konstantin.mueller@uni-wuerzburg.de"


@pytest.mark.parametrize("bad", ["a@b", "@uni.de", "a b@uni.de", "a@@uni.de", "x" * 150 + "@uni.de"])
def test_malformed_addresses_are_rejected(bad):
    with pytest.raises(ValueError):
        _email_login(bad)
