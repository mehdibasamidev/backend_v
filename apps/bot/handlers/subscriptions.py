from asgiref.sync import sync_to_async
from telegram import Update
from telegram.ext import ContextTypes

from apps.vpn.models import UserVpnSubscription
from apps.bot.services.registration import get_or_create_telegram_user
from apps.bot.services.keyboards import main_menu_keyboard

STATUS_LABELS_FA = {
    "pending_payment": "⏳ منتظر پرداخت",
    "pending_approval": "🔍 در حال بررسی ادمین",
    "active": "✅ فعال",
    "expired": "⛔️ منقضی",
    "rejected": "❌ رد شده",
    "cancelled": "لغو شده",
}


async def list_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    def _get():
        profile = get_or_create_telegram_user(update.effective_user)
        return list(
            UserVpnSubscription.objects
            .filter(user=profile.user)
            .select_related("plan")
            .order_by("-created_at")[:20]
        )

    subs = await sync_to_async(_get)()

    if not subs:
        await query.edit_message_text("هنوز هیچ سرویسی نداری.", reply_markup=main_menu_keyboard())
        return

    lines = []
    for sub in subs:
        title = sub.label or (sub.plan.name if sub.plan else "پلن سفارشی")
        status_fa = STATUS_LABELS_FA.get(sub.status, sub.status)
        line = f"📦 {title} — {status_fa}"
        if sub.status == "active":
            volume_text = "نامحدود" if sub.is_unlimited_volume else f"{sub.remaining_volume_gb} گیگ باقی‌مونده"
            line += f"\n{volume_text} | {sub.remaining_days} روز باقی‌مونده"
            if sub.subscription_link:
                line += f"\nلینک ساب: {sub.subscription_link}"
        lines.append(line)

    await query.edit_message_text("\n\n".join(lines), reply_markup=main_menu_keyboard())
