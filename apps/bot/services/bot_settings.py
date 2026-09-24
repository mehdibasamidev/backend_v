"""
The Telegram bot's @username (TelegramBotSettings): what "Login with
Telegram" and "Connect Telegram" put in their t.me deep links.

Shared by the admin API (apps/bot/api_views.py), the Django admin form,
telegram_auth.py and the bot container's startup (telegram_polling, and
bot_app.get_application for webhook mode). Migration bot 0006 carries a
frozen copy of normalize_bot_username.
"""

import asyncio
import logging
import re

from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.utils import timezone

from apps.bot.models import TelegramBotSettings

logger = logging.getLogger("apps")

BOT_USERNAME_ERROR = (
    "Enter the bot's username, e.g. my_vpn_bot. Telegram bot usernames end in 'bot'."
)

# Telegram's rule for a bot username: 5-32 characters of A-Z a-z 0-9 _,
# starting with a letter and ending in "bot" (checked separately, any case).
# ASCII classes rather than \w, which would let other scripts' letters in.
_BOT_USERNAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}")

# A link to the bot as copied from Telegram or BotFather: t.me/name,
# https://t.me/name/, https://t.me/name?start=x. Only the name is kept.
_BOT_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t|telegram)\.me/([^/?#]*)/?(?:[?#].*)?",
    re.IGNORECASE,
)


def is_valid_bot_username(value):
    return bool(
        _BOT_USERNAME_RE.fullmatch(value) and value.lower().endswith("bot")
    )


def normalize_bot_username(raw):
    """
    The bare username an admin meant, or "" for an empty entry (which
    clears the admin value). Accepts "name", "@name", "t.me/name" and
    "https://t.me/name" with a trailing slash or query. Raises
    django.core.exceptions.ValidationError with BOT_USERNAME_ERROR for
    anything else.

    Case is kept as typed: t.me links ignore it, and the admin panel shows
    back what was entered.
    """
    value = "" if raw is None else str(raw).strip()
    if not value:
        return ""

    link = _BOT_LINK_RE.fullmatch(value)
    if link:
        value = link.group(1)
    value = value.removeprefix("@")

    if not is_valid_bot_username(value):
        raise ValidationError(BOT_USERNAME_ERROR, code="invalid_bot_username")
    return value


def get_bot_settings():
    return TelegramBotSettings.get_solo()


def effective_bot_username():
    """The username deep links are built with, or "" when nobody has set or detected one."""
    return get_bot_settings().effective_bot_username


def set_admin_bot_username(raw):
    """
    Saves the admin's value ("" clears it, so the detected one applies
    again). Raises ValidationError for an invalid entry. Returns the row.
    """
    username = normalize_bot_username(raw)
    row = get_bot_settings()
    row.bot_username = username
    # Only the admin's field: a full save would write back the detected_*
    # values read above, undoing a bot container start that recorded new
    # ones in between.
    row.save(update_fields=["bot_username", "updated_at"])
    return row


def record_running_bot(username):
    """
    Records the username of the bot this container runs (from getMe).
    Never touches bot_username: what an admin typed always wins, and a
    difference is only warned about - here in the log, and in the admin
    panel through matches_running_bot.

    Returns the row, or None when the username isn't one to record. Raises
    TelegramBotSettings.DoesNotExist while the row doesn't exist yet
    (migration bot 0006 creates it), and the database's own error while the
    table doesn't: the polling container retries both
    (arecord_running_bot_until_done).
    """
    username = (username or "").strip()
    if not username:
        return None
    if not is_valid_bot_username(username):
        # Can't come from Telegram; guards the column against whatever else
        # a caller might pass.
        logger.warning("Not recording %r as the bot's username: not a bot username.", username)
        return None

    # A plain UPDATE, never get_solo(): the bot container doesn't wait for
    # migrate, and get_or_create run between bot 0005's and 0006's commits
    # wouldn't see the row 0006 is inserting and would insert a second one -
    # after which every get_solo() raises MultipleObjectsReturned. It also
    # leaves bot_username and updated_at alone, so an admin saving at the
    # same moment keeps their value.
    updated = TelegramBotSettings.objects.update(
        detected_bot_username=username, detected_at=timezone.now(),
    )
    if not updated:
        raise TelegramBotSettings.DoesNotExist(
            "No Telegram bot settings row yet (migration bot 0006 creates it)."
        )

    # Read back only for the mismatch warning.
    row = TelegramBotSettings.objects.first()
    if row is not None and row.matches_running_bot is False:
        logger.warning(
            "Telegram bot username mismatch: the admin set @%s, but this "
            "container's token belongs to @%s. \"Login with Telegram\" and "
            "\"Connect Telegram\" links still go to @%s. Correct it in the "
            "admin panel (Settings tab), or clear it there to use @%s.",
            row.bot_username, username, row.bot_username, username,
        )
    else:
        logger.info("Recorded @%s as the running Telegram bot.", username)
    return row


def _running_bot_username(bot):
    """bot.username of the initialised PTB Bot (initialize() ran getMe), or "" when unreadable."""
    try:
        return bot.username or ""
    except Exception:
        # PTB raises RuntimeError until initialize() has run.
        logger.warning("Could not read the bot's username (not initialised?); not recorded.")
        return ""


async def arecord_running_bot(bot):
    """
    One record_running_bot, for webhook mode (bot_app.get_application): that
    runs in the web container, whose app.sh only starts Gunicorn after
    migrate, so the row is there.

    Never raises: the webhook must keep being served even when this can't
    be written.
    """
    username = _running_bot_username(bot)
    if not username:
        return
    try:
        await sync_to_async(record_running_bot)(username)
    except Exception:
        logger.exception("Could not record @%s as the running Telegram bot.", username)


def _record_from_bot_container(username):
    # Same as proof_notifier._due_proof_ids: this process never runs Django's
    # request cycle, whose signals normally drop a broken connection - such
    # as one refused while Postgres was still starting.
    close_old_connections()
    return record_running_bot(username)


async def arecord_running_bot_until_done(bot, *, interval=10, give_up_after=600):
    """
    record_running_bot for the long-polling container, retried until it is
    written. telegram_polling runs it as a background task next to the
    proof notifier, so the bot serves updates meanwhile.

    That container never runs migrate, and docker compose's depends_on only
    orders the starts: on a first install, or the release that adds bot
    0005-0006, it gets here within seconds while the web container is still
    waiting for Postgres or migrating. Every failure - no connection, no
    table, no row yet - is retried every `interval` seconds. After
    `give_up_after` seconds it logs an error and stops; the next restart of
    the container tries again.

    Never raises, except CancelledError when the bot shuts down.
    """
    username = _running_bot_username(bot)
    if not username:
        return
    loop = asyncio.get_running_loop()
    deadline = loop.time() + give_up_after
    while True:
        try:
            await sync_to_async(_record_from_bot_container)(username)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if loop.time() >= deadline:
                logger.exception(
                    "Gave up recording @%s as the running Telegram bot after "
                    "%ss. \"Login with Telegram\" and \"Connect Telegram\" need "
                    "it unless a username is set in the admin panel (Settings "
                    "tab); restart the bot container once the web container "
                    "has migrated.",
                    username, give_up_after,
                )
                return
            logger.warning(
                "Could not record @%s as the running Telegram bot yet (%s: %s); "
                "retrying in %ss.",
                username, type(exc).__name__, exc, interval,
            )
        await asyncio.sleep(interval)
