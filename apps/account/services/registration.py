from django.contrib.auth import get_user_model
from django.db import transaction

from apps.account.models import AuthSettings

User = get_user_model()


@transaction.atomic
def create_phone_user(phone_number):
    """
    Creates an account for a number whose owner just proved it with a code.

    No password: a phone signup has no way to choose one yet, and
    set_unusable_password is what lets the login backend tell "wrong
    password" apart from "this account never had one".

    Username stays null on purpose - onboarding makes the user pick it, and
    the router blocks the app until they do.
    """
    user = User(phone_number=phone_number, is_phone_verified=True)
    user.set_unusable_password()
    user.save()
    return user


@transaction.atomic
def create_email_user(email, raw_password=None, hashed_password=None, verified=False):
    """
    Creates an account for an email signup.

    Accepts an already-hashed password because the OTP path stashes the
    hash on the pending code rather than building a half-real User row -
    an abandoned signup must not squat on the email address.
    """
    user = User(email=email.strip().lower(), is_email_verified=verified)
    if hashed_password:
        user.password = hashed_password
    elif raw_password:
        user.set_password(raw_password)
    else:
        user.set_unusable_password()
    user.save()
    return user


def email_otp_required():
    return AuthSettings.get_solo().email_otp_required
