from asgiref.sync import sync_to_async
from django.conf import settings
from telegram import Update
from telegram.ext import ContextTypes

from apps.vpn.models import VpnPricingConfig
from apps.vpn.services.pricing import calculate_custom_plan_price
from apps.bot.services.registration import get_or_create_telegram_user
from apps.bot.services.keyboards import custom_plan_text, custom_plan_keyboard


def _parse_payload(data: str):
    _, payload = data.split(":", 1)
    gb, days, users = (int(x) for x in payload.split(":"))
    return gb, days, users


async def start_builder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    config = await sync_to_async(VpnPricingConfig.get_active)()
    gb, days, users = config.min_gb, config.min_days, config.min_users

    await query.edit_message_text(
        custom_plan_text(gb, days, users),
        reply_markup=custom_plan_keyboard(gb, days, users, config),
    )


async def adjust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    gb, days, users = _parse_payload(query.data)

    config = await sync_to_async(VpnPricingConfig.get_active)()
    await query.edit_message_text(
        custom_plan_text(gb, days, users),
        reply_markup=custom_plan_keyboard(gb, days, users, config),
    )


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Like buy_plan, this only records the chosen spec and asks for the
    receipt - the subscription itself is created once the receipt lands.
    """
    query = update.callback_query
    await query.answer()
    gb, days, users = _parse_payload(query.data)

    def _prepare():
        profile = get_or_create_telegram_user(update.effective_user)
        price = calculate_custom_plan_price(gb, days, users)
        profile.set_awaiting_action(f"checkout:custom:{gb}:{days}:{users}")
        return price

    try:
        price = await sync_to_async(_prepare)()
    except Exception as e:
        await query.edit_message_text(f"⚠️ خطا: {e}")
        return

    text = (
        f"🧩 پلن سفارشی: {gb} گیگ / {days} روز / {users} کاربر\n"
        f"مبلغ قابل پرداخت: {price} تومان\n\n"
        f"لطفاً این مبلغ رو کارت‌به‌کارت کن:\n"
        f"شماره کارت: {settings.PAYMENT_CARD_NUMBER}\n"
        f"به نام: {settings.PAYMENT_CARD_HOLDER}\n\n"
        f"بعد از پرداخت، عکس فیش (یا کد پیگیری) رو همین‌جا بفرست تا سفارشت ثبت بشه."
    )
    await query.edit_message_text(text)
