"""Keep bot-visible service usage fresh and send one renewal-cycle warning.

The panel remains the source of truth. This worker lives next to Telegram
long-polling because sending a customer message belongs to the one process
that owns the bot connection; it also means no separate cron container is
needed for reminders.
"""

import asyncio
import logging
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from apps.bot.services.formatting import rtl_line, service_label
from apps.vpn.models import (
    PaymentProof,
    PaymentProofKindChoices,
    SubscriptionStatusChoices,
    UserVpnSubscription,
)
from apps.vpn.services.provisioning import sync_subscription_usage
from apps.vpn.services.review import apply_queued_renewal

logger = logging.getLogger("apps")


def _interval_seconds():
    return max(
        60,
        int(getattr(settings, "TELEGRAM_SUBSCRIPTION_MONITOR_INTERVAL_SECONDS", 900)),
    )


def _active_subscription_ids():
    close_old_connections()
    return list(
        UserVpnSubscription.objects.filter(
            status=SubscriptionStatusChoices.ACTIVE,
            hidden_at__isnull=True,
        )
        .exclude(xui_client_email__isnull=True)
        .exclude(xui_client_email="")
        .values_list("id", flat=True)
    )


def _claim_due_reminder(subscription_id):
    """Atomically reserve any due warning and return its chat/text payload.

    Two bot containers, or two ticks crossing each other, can therefore not
    send the same warning twice. A failed Telegram send releases the exact
    claim so the next interval can retry.
    """
    now = timezone.now()
    with transaction.atomic():
        subscription = (
            UserVpnSubscription.objects.select_for_update()
            .select_related("plan", "user__telegram_profile")
            .filter(id=subscription_id, status=SubscriptionStatusChoices.ACTIVE)
            .first()
        )
        if subscription is None:
            return None

        profile = getattr(subscription.user, "telegram_profile", None)
        if profile is None:
            return None

        remaining_volume = subscription.remaining_volume_gb
        has_usable_volume = (
            subscription.is_unlimited_volume or (remaining_volume is not None and remaining_volume > 0)
        )
        expiry_due = bool(
            subscription.expires_at
            and now < subscription.expires_at <= now + timedelta(days=2)
            and has_usable_volume
            and subscription.expiry_reminder_sent_at is None
        )
        volume_due = bool(
            not subscription.is_unlimited_volume
            and remaining_volume is not None
            and 0 < remaining_volume <= 1
            and subscription.low_volume_reminder_sent_at is None
        )
        # Re-arm a sent warning once its condition no longer holds. Renewals
        # through us clear both in reset_subscription_period, but an admin can
        # also top a client up on the panel directly; without this, that
        # service would never be warned again.
        rearm = []
        if subscription.expiry_reminder_sent_at and not (
            subscription.expires_at and subscription.expires_at <= now + timedelta(days=2)
        ):
            subscription.expiry_reminder_sent_at = None
            rearm.append("expiry_reminder_sent_at")
        if subscription.low_volume_reminder_sent_at and (
            subscription.is_unlimited_volume or (remaining_volume or 0) > 1
        ):
            subscription.low_volume_reminder_sent_at = None
            rearm.append("low_volume_reminder_sent_at")
        if rearm:
            subscription.save(update_fields=[*rearm, "updated_at"])

        if not expiry_due and not volume_due:
            return None
        # Already paid for the next period - it starts on its own when this
        # one runs out, so there is nothing to remind them about.
        if subscription.payment_proofs.filter(
            kind=PaymentProofKindChoices.RENEWAL, is_approved=True, applied_at__isnull=True,
        ).exists():
            return None

        title = service_label(subscription)
        details = []
        fields = []
        if expiry_due:
            details.append(f"⏰ فقط {subscription.remaining_days} روز تا پایان سرویس باقی مانده.")
            subscription.expiry_reminder_sent_at = now
            fields.append("expiry_reminder_sent_at")
        if volume_due:
            details.append(f"📶 فقط {remaining_volume} گیگ از حجم سرویس باقی مانده.")
            subscription.low_volume_reminder_sent_at = now
            fields.append("low_volume_reminder_sent_at")

        subscription.save(update_fields=[*fields, "updated_at"])
        return {
            "chat_id": profile.telegram_user_id,
            "text": (
                "🔔 یادآوری\n"
                + rtl_line(f"سرویس: {title}") + "\n\n"
                + "\n".join(details)
                + "\n\nبرای تمدید، وارد «سرویس‌های من» در بات شو."
            ),
            "claimed_at": now,
            "fields": fields,
        }


def _release_claim(subscription_id, claimed_at, fields):
    # Do not clear a newer claim if a second process retried after this one.
    updates = {field: None for field in fields}
    UserVpnSubscription.objects.filter(
        id=subscription_id,
        **{field: claimed_at for field in fields},
    ).update(**updates)


def _sync_and_claim(subscription_id):
    subscription = UserVpnSubscription.objects.get(id=subscription_id)
    sync_subscription_usage(subscription)
    return _claim_due_reminder(subscription_id)


# Queued renewals are checked far more often than the full sweep: between
# the period running out and the next check the customer is cut off, and
# there are only ever a handful of them.
_QUEUE_TICK_SECONDS = 60


def _queued_renewal_ids():
    close_old_connections()
    return list(
        PaymentProof.objects.filter(
            kind=PaymentProofKindChoices.RENEWAL, is_approved=True, applied_at__isnull=True,
        )
        .order_by("reviewed_at")
        .values_list("id", flat=True)
    )


def _renewal_started_message(subscription_id, proof_id):
    subscription = (
        UserVpnSubscription.objects.select_related("plan", "user__telegram_profile")
        .get(id=subscription_id)
    )
    profile = getattr(subscription.user, "telegram_profile", None)
    if profile is None:
        return None
    proof = PaymentProof.objects.get(id=proof_id)
    title = service_label(subscription)
    volume = "حجم نامحدود" if proof.extra_gb == 0 else f"{proof.extra_gb} گیگ"
    return profile.telegram_user_id, (
        "✅ دوره جدید سرویست فعال شد.\n"
        + rtl_line(f"سرویس: {title}") + "\n"
        + f"{volume} / {proof.extra_days} روز از همین الان."
        + (f"\nلینک ساب: {subscription.subscription_link}" if subscription.subscription_link else "")
    )


async def _apply_due_renewals(bot):
    for proof_id in await sync_to_async(_queued_renewal_ids)():
        try:
            subscription = await sync_to_async(apply_queued_renewal)(proof_id)
            if subscription is None:
                continue
            logger.info("Queued renewal %s applied to service %s", proof_id, subscription.id)
            message = await sync_to_async(_renewal_started_message)(subscription.id, proof_id)
            if message is not None:
                try:
                    await bot.send_message(chat_id=message[0], text=message[1])
                except Exception:
                    # The renewal is applied; a lost "it started" note is not
                    # worth re-running anything for.
                    logger.exception("Could not tell the customer that renewal %s started", proof_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Applying queued renewal %s failed; retrying next tick", proof_id)


async def _sweep(bot):
    for subscription_id in await sync_to_async(_active_subscription_ids)():
        try:
            reminder = await sync_to_async(_sync_and_claim)(subscription_id)
            if reminder is None:
                continue
            try:
                await bot.send_message(chat_id=reminder["chat_id"], text=reminder["text"])
            except BaseException:
                await asyncio.shield(sync_to_async(_release_claim)(
                    subscription_id, reminder["claimed_at"], reminder["fields"],
                ))
                raise
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Subscription monitor failed for service %s", subscription_id)


async def run_subscription_monitor(bot):
    """
    Every minute: start queued renewals whose period has run out.
    Every TELEGRAM_SUBSCRIPTION_MONITOR_INTERVAL_SECONDS: sync all active
    services from the panel and send due reminders.
    """
    interval = _interval_seconds()
    loop = asyncio.get_running_loop()
    last_sweep = None
    while True:
        try:
            await _apply_due_renewals(bot)
            if last_sweep is None or loop.time() - last_sweep >= interval:
                last_sweep = loop.time()
                await _sweep(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Subscription monitor tick failed")
        await asyncio.sleep(_QUEUE_TICK_SECONDS)
