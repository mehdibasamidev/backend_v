from asgiref.sync import sync_to_async
from django.conf import settings
from django.db.models import Exists, OuterRef
from telegram import Update
from telegram.ext import ContextTypes

from apps.bot.services.formatting import (
    RLM,
    client_name,
    fa_number,
    fa_price,
    rtl_line,
    service_label,
    service_name,
)
from apps.bot.services.keyboards import main_menu_keyboard, subscriptions_keyboard
from apps.bot.services.registration import get_or_create_telegram_user
from apps.vpn.models import PaymentProof, UserVpnSubscription
from apps.vpn.services.checkout import (
    current_period_used_up,
    default_renewal_quote,
    renewal_info,
)
from apps.vpn.services.pricing import RenewalUnavailable
from apps.vpn.services.lazy_sync import lazy_sync


STATUS_LABELS_FA = {
    "pending_payment": "⏳ منتظر پرداخت",
    "pending_approval": "🔍 در حال بررسی ادمین",
    "active": "✅ فعال",
    "expired": "⛔️ منقضی",
    "rejected": "❌ رد شده",
    "cancelled": "لغو شده",
}


def _amounts(days, volume_gb):
    volume = "حجم نامحدود" if volume_gb == 0 else f"{volume_gb} گیگ"
    return f"{volume} / {days} روز"


async def list_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    def _get():
        profile = get_or_create_telegram_user(update.effective_user)
        if profile is None:
            return None

        subscriptions = list(
            UserVpnSubscription.objects
            .filter(user=profile.user)
            .select_related("plan")
            .annotate(
                has_pending_proof=Exists(
                    PaymentProof.objects.filter(
                        subscription_id=OuterRef("pk"), is_approved__isnull=True,
                    )
                )
            )
            .order_by("-created_at")[:20]
        )
        # The old handler showed only stored figures. This refreshes stale
        # active services from 3x-ui before rendering; it is throttled to one
        # panel request/minute per service to protect the panel on refreshes.
        subscriptions = lazy_sync(subscriptions)
        for subscription in subscriptions:
            subscription.renewal = renewal_info(subscription)
        return subscriptions

    subs = await sync_to_async(_get)()

    if update.callback_query:
        await update.callback_query.answer()

    if subs is None:
        text = (
            "برای خرید اول باید ثبت‌نام کنی.\n"
            "دستور /start رو بزن و کد معرفت رو وارد کن."
        )
        if update.callback_query:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return

    if not subs:
        text = "هنوز هیچ سرویسی نداری."
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text, reply_markup=main_menu_keyboard(),
            )
        else:
            await update.message.reply_text(text, reply_markup=main_menu_keyboard())
        return

    lines = []
    for subscription in subs:
        status_fa = STATUS_LABELS_FA.get(subscription.status, subscription.status)
        rows = []
        if subscription.xui_client_email:
            rows.append(rtl_line(f"🆔{RLM}  {client_name(subscription.xui_client_email)}"))
        # RLM after the emoji too: "ℹ️" is itself a left-to-right
        # character and would pull a leading "۲۰" in the name over to it.
        rows.append(rtl_line(f"ℹ️{RLM} {service_name(subscription)}"))
        rows.append(rtl_line(status_fa.replace(" ", "  ", 1)))
        if subscription.status == "active":
            volume_text = (
                "حجم نامحدود"
                if subscription.is_unlimited_volume
                else f"{fa_number(subscription.remaining_volume_gb)} گیگ باقی‌مونده"
            )
            rows.append(rtl_line(
                f"{volume_text} | {fa_number(subscription.remaining_days)} روز باقی‌مونده"
            ))
            if subscription.subscription_link:
                rows.append(rtl_line(f"لینک ساب: {subscription.subscription_link}"))
        line = "\n".join(rows)
        if subscription.has_pending_proof:
            line += "\n⏳ یک فیش در انتظار بررسی داری"
        queued = subscription.renewal["queued_renewal"]
        if queued:
            line += (
                f"\n🔁 تمدید پرداخت‌شده: {_amounts(queued['days'], queued['volume_gb'])}"
                " — وقتی حجم یا روز فعلی تموم بشه خودکار فعال میشه"
            )
        lines.append(line)

    text = "\n\n".join(lines)
    keyboard = subscriptions_keyboard(subs)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)


RENEWAL_UNAVAILABLE_FA = {
    "not_activated": "این سرویس هنوز فعال نشده.",
    "pending_review": "یک فیش برای این سرویس در انتظار بررسی ادمینه.",
    "already_queued": "تمدید این سرویس قبلاً پرداخت شده و بعد از تموم شدن دوره فعلی خودکار فعال میشه.",
    "plan_retired": "این پلن دیگه فروخته نمیشه. لطفاً یکی از پلن‌های فعلی رو بخر.",
    "pricing_unavailable": "قیمت‌گذاری پلن سفارشی تغییر کرده و این ترکیب دیگه قابل تمدید نیست. لطفاً پلن جدید بخر.",
}


async def start_renewal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quote the renewal (the service's own plan at today's price) and await the receipt."""
    query = update.callback_query
    await query.answer()
    subscription_id = query.data.removeprefix("sub:renew:")

    def _prepare():
        profile = get_or_create_telegram_user(update.effective_user)
        if profile is None:
            return None
        subscription, extra_days, extra_gb, price = default_renewal_quote(
            user=profile.user, subscription_id=subscription_id,
        )
        starts_now = current_period_used_up(subscription)
        label = service_label(subscription)
        profile.set_awaiting_action(f"renew:{subscription.id}")
        return subscription, extra_days, extra_gb, price, starts_now, label

    try:
        prepared = await sync_to_async(_prepare)()
    except RenewalUnavailable as exc:
        await query.edit_message_text(
            f"⚠️ {RENEWAL_UNAVAILABLE_FA.get(exc.reason, exc.message)}",
            reply_markup=main_menu_keyboard(),
        )
        return
    except Exception:
        # Callback data is normally ours, but a stale or forged button can
        # carry someone else's or an invalid id - never show a traceback.
        await query.edit_message_text(
            "⚠️ سرویس پیدا نشد یا فعلاً قابل تمدید نیست.",
            reply_markup=main_menu_keyboard(),
        )
        return

    if prepared is None:
        await query.edit_message_text("برای تمدید باید اول ثبت‌نام کنی.")
        return

    subscription, extra_days, extra_gb, price, starts_now, label = prepared
    amounts = _amounts(extra_days, extra_gb)
    if starts_now:
        when = (
            f"بلافاصله بعد از تایید ادمین، سرویست از نو {amounts} شارژ میشه."
            "\nاگه چیزی از حجم یا روز قبلی مونده باشه، به دوره جدید اضافه نمیشه."
        )
    else:
        when = (
            "هنوز از دوره فعلی حجم و روز داری و تا آخرش قابل استفاده‌ست."
            f"\nدوره جدید ({amounts}) خودکار از لحظه‌ای شروع میشه که حجم یا روز فعلی"
            " (هر کدوم زودتر) تموم بشه."
        )
    await query.edit_message_text(
        "🔁 تمدید سرویس\n\n"
        f"سرویس: {label}\n"
        f"تمدید: {amounts}\n"
        f"مبلغ قابل پرداخت: {fa_price(price)}\n\n"
        f"{when}\n\n"
        "لطفاً مبلغ را کارت‌به‌کارت کن:\n"
        f"شماره کارت: {settings.PAYMENT_CARD_NUMBER}\n"
        f"به نام: {settings.PAYMENT_CARD_HOLDER}\n\n"
        "بعد از پرداخت، عکس فیش یا کد پیگیری را همین‌جا بفرست.",
    )
