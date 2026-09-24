"""
Creates the TelegramBotSettings row and seeds its bot_username from
TELEGRAM_BOT_USERNAME.

That variable used to be read on every "Login with Telegram" / "Connect
Telegram". The username now lives in the database - set in the admin
panel's Settings tab or the Django admin, and filled in by the bot container
when it starts - and settings.py no longer defines it, so it is read here
exactly once, straight from the environment. A deploy that had it set keeps
the same links without anyone re-entering it. Once this has run, the
variable can be deleted from .env.

Creating the row here also means get_solo() only ever reads it: two first
requests racing in get_or_create could otherwise insert two rows. The bot
container, which doesn't wait for migrate, never calls get_solo() for the
same reason: it records its username with a plain UPDATE and retries until
this row exists (bot_settings.record_running_bot).
"""

import re
import sys

from decouple import config
from django.db import migrations

# A frozen copy of apps/bot/services/bot_settings.normalize_bot_username.
# Migrations must not import app code: it changes or disappears later, and
# this migration still has to run on a fresh database then.
_BOT_USERNAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}")
_BOT_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t|telegram)\.me/([^/?#]*)/?(?:[?#].*)?",
    re.IGNORECASE,
)


def _normalize(raw):
    """The bare username, "" for an empty value, None for an invalid one."""
    value = (raw or "").strip()
    if not value:
        return ""
    link = _BOT_LINK_RE.fullmatch(value)
    if link:
        value = link.group(1)
    value = value.removeprefix("@")
    if _BOT_USERNAME_RE.fullmatch(value) and value.lower().endswith("bot"):
        return value
    return None


def seed_bot_settings(apps, schema_editor):
    TelegramBotSettings = apps.get_model("bot", "TelegramBotSettings")

    row = TelegramBotSettings.objects.first()
    if row is None:
        row = TelegramBotSettings.objects.create()

    raw = config("TELEGRAM_BOT_USERNAME", default="")
    username = _normalize(raw)
    if username is None:
        # Skipped rather than raised, unlike vpn 0005's inbound ids: a
        # failed migrate stops app.sh (set -e) before Gunicorn starts, so
        # the whole API stays down, while a missing username only disables
        # Telegram sign-in until an admin sets it in the admin panel or the
        # bot container records it at its next start.
        print(
            f"\n  WARNING: TELEGRAM_BOT_USERNAME={raw!r} is not a bot username "
            "(5-32 letters, digits or _, ending in 'bot'); not copied. Set it "
            "in the admin panel (Settings tab) or let the bot container fill it in.",
            file=sys.stderr,
        )
        return
    if username and not row.bot_username:
        row.bot_username = username
        row.save(update_fields=["bot_username", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("bot", "0005_telegrambotsettings"),
    ]

    operations = [
        migrations.RunPython(seed_bot_settings, migrations.RunPython.noop),
    ]
