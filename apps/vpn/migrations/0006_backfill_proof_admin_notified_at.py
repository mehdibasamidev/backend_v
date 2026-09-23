"""
Back-fills PaymentProof.admin_notified_at, the Telegram announcement outbox
added in 0004.

Before this change only receipts sent through the bot were posted to the
admins. So every existing proof counts as announced EXCEPT a pending one
whose buyer has no Telegram profile: that one came from the app and never
reached Telegram. Leaving it empty makes the bot container post it once.

Deploy order: STOP the bot container before the web container migrates,
and start the new one only after `migrate` has finished:

    docker compose stop spacedigital_vpn_telegram_bot
    docker compose up -d --build spacedigital_vpn_django   # app.sh migrates
    docker compose up -d --build spacedigital_vpn_telegram_bot

Two things go wrong otherwise, both as a receipt posted twice with live
buttons in every admin chat:
  - the OLD bot keeps posting receipts without setting admin_notified_at.
    One sent after this migration ran stays NULL, and the new poller posts
    it again;
  - 0004 and this migration commit separately, so a NEW bot polling during
    `migrate` can see the empty column in between and repost an old
    pending bot receipt.
"""

from django.db import migrations
from django.db.models import Q
from django.utils import timezone


def backfill_admin_notified_at(apps, schema_editor):
    PaymentProof = apps.get_model("vpn", "PaymentProof")
    TelegramProfile = apps.get_model("bot", "TelegramProfile")

    telegram_user_ids = TelegramProfile.objects.filter(user__isnull=False).values("user_id")
    never_announced = Q(is_approved__isnull=True) & ~Q(subscription__user_id__in=telegram_user_ids)

    PaymentProof.objects.filter(admin_notified_at__isnull=True).exclude(never_announced).update(
        admin_notified_at=timezone.now()
    )


class Migration(migrations.Migration):

    dependencies = [
        ("vpn", "0005_seed_default_inbound_group"),
        ("bot", "0002_alter_telegramprofile_user"),
    ]

    operations = [
        migrations.RunPython(backfill_admin_notified_at, migrations.RunPython.noop),
    ]
