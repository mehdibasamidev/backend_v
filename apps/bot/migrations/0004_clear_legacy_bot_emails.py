"""
Clears the placeholder email the first bot version gave every account it
created: tg<telegram id>@telegram.local.

Nothing can receive mail there, and since "Login with Telegram" those
accounts reach the app, where the address showed up as their email, kept
the "verify your email" banner on for good (when email verification is
switched on), and made "add email" refuse with "already has an email". The
bot has stopped creating it (registration._create_user: "No synthetic
email"); this removes what is left, so an account created in the bot looks
the same whichever version created it.

Only an exact match is cleared: the address built from that account's own
Telegram id, on an account whose password is unusable. With a usable
password the address would be a working email + password sign-in, and
clearing it would lock the owner out.

Not reversed: the rows it cleared can't be told apart from bot accounts
that never had the placeholder, and nothing reads it any more.
"""

from django.db import migrations

# django.contrib.auth.hashers.UNUSABLE_PASSWORD_PREFIX. Historical models
# have no has_usable_password(), so the stored value is checked directly.
_UNUSABLE_PASSWORD_PREFIX = "!"


def clear_placeholder_emails(apps, schema_editor):
    TelegramProfile = apps.get_model("bot", "TelegramProfile")
    User = apps.get_model("account", "User")

    profiles = (
        TelegramProfile.objects.filter(
            user__isnull=False,
            user__email__iendswith="@telegram.local",
            user__password__startswith=_UNUSABLE_PASSWORD_PREFIX,
        )
        .values_list("user_id", "user__email", "telegram_user_id")
    )
    user_ids = [
        user_id
        for user_id, email, telegram_user_id in profiles
        if email.lower() == f"tg{telegram_user_id}@telegram.local"
    ]
    User.objects.filter(pk__in=user_ids).update(email=None, is_email_verified=False)


class Migration(migrations.Migration):

    dependencies = [
        ("account", "0003_alter_user_identifier"),
        ("bot", "0003_telegramauthrequest"),
    ]

    operations = [
        migrations.RunPython(clear_placeholder_emails, migrations.RunPython.noop),
    ]
