from asgiref.sync import sync_to_async
from django.conf import settings
from telegram import Update
from telegram.ext import ContextTypes

from apps.vpn.models import VpnPlan
from apps.bot.services.registration import get_or_create_telegram_user
from apps.bot.services.keyboards import plans_list_keyboard, plan_detail_keyboard


async def show_plan_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = await sync_to_async(plans_list_keyboard)()
    await query.edit_message_text("یکی از پلن‌های زیر رو انتخاب کن:", reply_markup=keyboard)


async def show_plan_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plan_id = query.data.split(":")[-1]

    def _get():
        return VpnPlan.objects.get(id=plan_id, is_active=True)

    try:
        plan = await sync_to_async(_get)()
    except VpnPlan.DoesNotExist:
        await query.edit_message_text("این پلن دیگه موجود نیست.")
        return

    users_text = "بدون محدودیت" if plan.max_concurrent_users == 0 else str(plan.max_concurrent_users)
    volume_text = "نامحدود" if plan.volume_gb == 0 else f"{plan.volume_gb} گیگ"
    text = (
        f"📦 {plan.name}\n"
        f"حجم: {volume_text}\n"
        f"مدت: {plan.duration_days} روز\n"
        f"کاربر همزمان: {users_text}\n"
        f"قیمت: {plan.price} تومان"
    )
    await query.edit_message_text(text, reply_markup=plan_detail_keyboard(plan.id))


async def buy_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Does NOT create the subscription yet - it only remembers which plan the
    user picked and asks for the receipt. The order is created atomically
    once the receipt actually arrives (see handlers/payment.py), so people
    who browse and leave never produce an orphan pending record.
    """
    query = update.callback_query
    await query.answer()
    plan_id = query.data.split(":")[-1]

    def _prepare():
        profile = get_or_create_telegram_user(update.effective_user)
        try:
            plan = VpnPlan.objects.get(id=plan_id, is_active=True)
        except VpnPlan.DoesNotExist:
            return None
        profile.set_awaiting_action(f"checkout:fixed:{plan.id}")
        return plan

    plan = await sync_to_async(_prepare)()
    if plan is None:
        await query.edit_message_text("این پلن دیگه موجود نیست.")
        return

    text = (
        f"📦 {plan.name}\n"
        f"مبلغ قابل پرداخت: {plan.price} تومان\n\n"
        f"لطفاً این مبلغ رو کارت‌به‌کارت کن:\n"
        f"شماره کارت: {settings.PAYMENT_CARD_NUMBER}\n"
        f"به نام: {settings.PAYMENT_CARD_HOLDER}\n\n"
        f"بعد از پرداخت، عکس فیش (یا کد پیگیری) رو همین‌جا بفرست تا سفارشت ثبت بشه."
    )
    await query.edit_message_text(text)
