"""
Back-fills PaymentProof.source, added in 0007 with default "app".

Until now the source was never stored: the admin announcement guessed it
from the buyer having a TelegramProfile. For existing rows that guess is
exact - before Telegram linking a bot account had no way into the app and
an app account had no TelegramProfile - so it is applied once here, and
from now on the caller records the source.
"""

from django.db import migrations


def backfill_source(apps, schema_editor):
    PaymentProof = apps.get_model("vpn", "PaymentProof")
    TelegramProfile = apps.get_model("bot", "TelegramProfile")

    telegram_user_ids = TelegramProfile.objects.filter(user__isnull=False).values("user_id")
    PaymentProof.objects.filter(subscription__user_id__in=telegram_user_ids).update(source="bot")


class Migration(migrations.Migration):

    dependencies = [
        ("vpn", "0007_paymentproof_source"),
        ("bot", "0002_alter_telegramprofile_user"),
    ]

    operations = [
        # Reversing needs nothing: going back past 0007 drops the column.
        migrations.RunPython(backfill_source, migrations.RunPython.noop),
    ]
