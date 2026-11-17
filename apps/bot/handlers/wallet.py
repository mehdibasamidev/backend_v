from decimal import Decimal

from asgiref.sync import sync_to_async
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from apps.bot.services.formatting import fa_decimal_amount, fa_number
from apps.bot.services.registration import get_or_create_telegram_user
from apps.referral.services.rewards import wallet_summary


async def show_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_chat.type != "private":
        if query:
            await query.answer("اعتبار فقط توی چت خصوصی نمایش داده میشه.", show_alert=True)
        return
    if query:
        await query.answer()

    def _load():
        profile = get_or_create_telegram_user(update.effective_user)
        return wallet_summary(profile.user) if profile else None

    summary = await sync_to_async(_load)()
    if summary is None:
        text = "برای دیدن اعتبار، اول با /start ثبت‌نام کن."
        keyboard = None
    else:
        lines = ["💰 اعتبار من", f"موجودی قابل استفاده: {fa_decimal_amount(summary['balance'])} تومان"]
        if Decimal(summary["reserved"]) > 0:
            lines.append(f"رزروشده برای سفارش‌های در حال بررسی: {fa_decimal_amount(summary['reserved'])} تومان")
        discounts = summary["available_discounts"]
        if discounts:
            lines.append(f"تخفیف‌های قابل استفاده: {fa_number(len(discounts))} عدد")
            lines.append(f"بیشترین تخفیف: {fa_decimal_amount(discounts[0])}٪")
        else:
            lines.append("فعلاً تخفیف قابل استفاده نداری.")
        lines.append("\nمی‌تونی اعتبار یا تخفیفت رو موقع خرید سرویس جدید در اپ استفاده کنی.")
        text = "\n".join(lines)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ برگشت", callback_data="menu:main")],
        ]) if query else None

    if query:
        await query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text)
