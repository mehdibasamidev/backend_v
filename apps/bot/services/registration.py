import logging

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.bot.models import TelegramProfile
from apps.referral.services.redemption import (
    redeem,
    referral_required,
    resolve_code,
)
from config.utils.exceptions import AppException

User = get_user_model()
logger = logging.getLogger("apps")

# Stored in TelegramProfile.awaiting_action, the same field the checkout
# flow uses. One state machine per user rather than two competing ones.
AWAITING_REFERRAL_CODE = "awaiting_referral_code"


def get_or_create_telegram_user(telegram_user):
    """
    Returns the TelegramProfile for this Telegram account, creating the
    profile and its Django User if needed.

    Returns a PROFILE, not a User - callers read profile.user and call
    profile.set_awaiting_action(), so changing that would break every
    handler.

    Returns None when invite codes are required and this person hasn't
    supplied one yet. Callers must handle that; require_user() below makes
    it hard to forget.
    """
    profile = (
        TelegramProfile.objects.filter(telegram_user_id=telegram_user.id)
        .select_related("user")
        .first()
    )
    if profile and profile.user_id:
        _refresh_profile_names(profile, telegram_user)
        return profile

    if referral_required():
        # Nothing is created yet. /start asks for a code, and the reply
        # handler finishes the job.
        return None

    return _create_user(telegram_user)


def needs_referral_code(telegram_user_id):
    """True when this person has started but not yet given a valid code."""
    profile = TelegramProfile.objects.filter(
        telegram_user_id=telegram_user_id
    ).first()
    return bool(profile and profile.awaiting_action == AWAITING_REFERRAL_CODE)


def begin_referral_prompt(telegram_user):
    """
    Marks this Telegram account as waiting for a code, so the next plain
    text message is read as that rather than falling through to the
    checkout handler.
    """
    profile, _ = TelegramProfile.objects.get_or_create(
        telegram_user_id=telegram_user.id,
        defaults={
            "telegram_username": telegram_user.username or "",
            "telegram_first_name": telegram_user.first_name or "",
        },
    )
    profile.set_awaiting_action(AWAITING_REFERRAL_CODE)
    return profile


def submit_referral_code(telegram_user, raw_code):
    """
    Validates a code and, on success, creates the account and records the
    redemption together.

    Returns (True, user) or (False, error_message). Nothing is written on
    failure, so a typo leaves no half-account for the retry to collide
    with.
    """
    try:
        code = resolve_code(raw_code)
    except AppException as exc:
        return False, exc.message

    try:
        with transaction.atomic():
            profile = _create_user(telegram_user)
            redeem(code.code, profile.user)
    except AppException as exc:
        # Raised by redeem() when the code was used up between the check
        # above and the lock inside it. The transaction rolls the user back
        # so the person can try another code from a clean slate.
        return False, exc.message

    return True, profile.user


def _create_user(telegram_user):
    """
    Mints the Django account and links the profile.

    No synthetic email: the account model allows every identifier to be
    null, so a Telegram user with none set is honest about what is actually
    known - rather than stranded behind an email-verification prompt for an
    address that could never receive a code.
    """
    user = User(
        full_name=" ".join(
            filter(None, [telegram_user.first_name, telegram_user.last_name])
        ),
    )
    user.set_unusable_password()
    user.save()

    profile, _ = TelegramProfile.objects.update_or_create(
        telegram_user_id=telegram_user.id,
        defaults={
            "user": user,
            "telegram_username": telegram_user.username or "",
            "telegram_first_name": telegram_user.first_name or "",
        },
    )
    profile.clear_awaiting_action()
    return profile


def _refresh_profile_names(profile, telegram_user):
    """People rename themselves on Telegram; the admin list should follow."""
    username = telegram_user.username or ""
    first_name = telegram_user.first_name or ""
    if profile.telegram_username == username and (
        profile.telegram_first_name == first_name
    ):
        return
    profile.telegram_username = username
    profile.telegram_first_name = first_name
    profile.save(update_fields=[
        "telegram_username", "telegram_first_name", "updated_at",
    ])


def require_user(telegram_user):
    """
    For handlers that can't do anything without an account.

    Returns (profile, None) when there is one, or (None, message) with the
    text the handler should reply with instead. Every existing caller
    predates invite codes and assumed a profile always came back - this
    makes the "not registered yet" case impossible to forget rather than an
    AttributeError on None.
    """
    profile = get_or_create_telegram_user(telegram_user)
    if profile is not None:
        return profile, None

    return None, (
        "برای استفاده از ربات باید اول ثبت‌نام کنی.\n"
        "دستور /start رو بزن و کد معرفت رو وارد کن."
    )
