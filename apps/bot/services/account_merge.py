"""
Folds a Telegram-only account into an app account, for "Connect Telegram"
(apps/bot/services/telegram_auth.py) when the Telegram already bought
through the bot.

What moves to the target:
  - UserVpnSubscription.user - payment proofs hang off the subscription,
    so they follow without being touched;
  - the referral data: codes the source owns, redemptions it earned as
    referrer, and the redemption that brought it in (see below);
  - the TelegramProfile, re-pointed, so the bot keeps serving the same
    person from the target account.

What is freed on the source:
  - its username, if it has one. "Login with Telegram" lets a bot-only
    account into the app, where onboarding makes it pick a username (a
    username alone still leaves it bot-only). Left on the deactivated row,
    it would stop the same person taking that name on the target.

What deliberately stays on the source:
  - chat (rooms, messages, read and delivery marks): the agreed scope of
    a merge is services and referral data, and moving authorship rewrites
    what other people see in their history. A bot-only account can reach
    the chat through "Login with Telegram", so a conversation it had there
    stays with the deactivated account; whether chat participation should
    follow is an open question for the product owner, not settled here;
  - apps.payments, the legacy coach-payments app: its models are not even
    loaded (apps/payments/models has no __init__.py) and nothing routes to
    it, so there is nothing live to move;
  - OtpCode, TelegramAuthRequest, admin LogEntry, reviewed proofs, groups
    and permissions: short-lived rows, records of what the source did, or
    things only staff have - and a staff account is never bot-only. The
    confirmed link request that caused the merge names the source in
    merged_from.

The source is deactivated, never deleted: its user row is what old
payment evidence and referral history still point at.
"""

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.bot.models import TelegramProfile
from apps.referral.models import Referral, ReferralCode
from apps.vpn.models import UserVpnSubscription
from config.utils.exceptions import BadRequestException

User = get_user_model()
logger = logging.getLogger("apps")


def is_bot_only_account(user):
    """
    True when nothing but Telegram can sign into this account.

    A username alone doesn't count: without a password it can't be used to
    sign in. The placeholder tg<id>@telegram.local emails the first bot
    version stored are cleared by bot migration 0004, so those accounts
    pass the plain email check. Staff is excluded outright - merging would
    silently hand an admin's rights and history to another account.
    """
    return not (
        user.email
        or user.phone_number
        or user.google_id
        or user.has_usable_password()
        or user.is_staff
        or user.is_superuser
    )


@transaction.atomic
def merge_bot_account_into(source, target):
    """
    Moves everything listed in the module docstring from `source` to
    `target`, deactivates `source`, and returns how many subscriptions
    moved.

    Raises BadRequestException, changing nothing, when source is target or
    source can still be signed into on its own - merging a real account
    would strand whoever owns its email or phone.
    """
    if source.pk == target.pk:
        raise BadRequestException("An account can't be merged into itself.")

    # Both rows locked in primary-key order: two merges touching the same
    # pair in opposite directions would otherwise each hold one lock and
    # wait for the other. Re-read under the lock, so the bot-only check
    # below can't pass on a copy that has since been given a phone number.
    locked = {
        user.pk: user
        for user in User.objects.select_for_update()
        .filter(pk__in=[source.pk, target.pk])
        .order_by("pk")
    }
    source, target = locked[source.pk], locked[target.pk]

    if not is_bot_only_account(source):
        raise BadRequestException(
            "Only an account that exists solely in the Telegram bot can be merged."
        )

    source_profile = TelegramProfile.objects.filter(user=source).first()
    if source_profile is not None and TelegramProfile.objects.filter(user=target).exists():
        # One profile per user: the target would end up with two Telegrams.
        raise BadRequestException("The target account is already connected to Telegram.")

    now = timezone.now()
    moved = UserVpnSubscription.objects.filter(user=source).update(
        user=target, updated_at=now,
    )

    # Credit for people the source invited. A redemption where the target
    # itself was the one invited stays put: moving it would record the
    # target as having referred itself.
    Referral.objects.filter(referrer=source).exclude(referred_user=target).update(
        referrer=target,
    )
    # How the source was invited. referred_user is one-to-one, so this only
    # moves when the target has no invite of its own - and not when the
    # target is who invited it, which would again be a self-referral.
    if not Referral.objects.filter(referred_user=target).exists():
        Referral.objects.filter(referred_user=source).exclude(referrer=target).update(
            referred_user=target,
        )
    # Codes already shared keep working and now credit the target. If both
    # had a personal code the target keeps two; get_or_create_personal_code
    # shows the newest.
    ReferralCode.objects.filter(owner=source).update(owner=target)

    if source_profile is not None:
        source_profile.user = target
        source_profile.save(update_fields=["user", "updated_at"])

    if not target.full_name and source.full_name:
        target.full_name = source.full_name
        target.save(update_fields=["full_name"])

    freed_username = source.username
    source.username = None
    source.is_active = False
    source.save(update_fields=["username", "is_active"])

    # The confirmed TelegramAuthRequest that triggered this is kept and
    # records both accounts (merged_from); the log line names the freed
    # username, which nothing else keeps.
    logger.info(
        "Merged bot-only account %s into %s (%s subscriptions moved, username %r freed)",
        source.pk, target.pk, moved, freed_username,
    )
    return moved
