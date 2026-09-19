from asgiref.sync import sync_to_async
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from urllib.parse import urlencode
from apps.bot.services.keyboards import main_menu_keyboard
from apps.bot.services.registration import (
    get_or_create_telegram_user,
    needs_referral_code,
    submit_referral_code,
)
from apps.referral.services.redemption import get_or_create_personal_code
from config.utils.exceptions import AppException


async def try_handle_referral_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Consumes a plain text message as an invite code, if that's what this
    person was asked for.

    Returns True when the message was handled here - the caller must stop,
    or the same text would also be read as a payment receipt. Returns False
    to let other text handlers have it.
    """
    telegram_user = update.effective_user

    if not await sync_to_async(needs_referral_code)(telegram_user.id):
        return False

    raw_code = (update.message.text or "").strip()
    ok, result = await sync_to_async(submit_referral_code)(
        telegram_user, raw_code
    )

    if not ok:
        # Re-prompt rather than dropping them out of the flow: the
        # alternative is making someone type /start again over a typo.
        await update.message.reply_text(
            f"❌ {result}\n\nیه کد دیگه امتحان کن."
        )
        return True

    await update.message.reply_text(
        "✅ خوش اومدی! حسابت ساخته شد.",
        reply_markup=main_menu_keyboard(),
    )
    return True


async def my_referral_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the caller's own invite code, with a share button."""

    if update.callback_query:
        await update.callback_query.answer()

    profile = await sync_to_async(get_or_create_telegram_user)(
        update.effective_user
    )

    if profile is None:
        text = "اول باید ثبت‌نام کنی. /start رو بزن."
        if update.callback_query:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return

    try:
        code = await sync_to_async(get_or_create_personal_code)(profile.user)
    except AppException as exc:
        if update.callback_query:
            await update.callback_query.edit_message_text(exc.message)
        else:
            await update.message.reply_text(exc.message)
        return

    remaining = (
        "نامحدود" if code.max_uses == 0 else f"{code.remaining_uses} بار"
    )
    referral_link = f"https://t.me/{context.bot.username}?start={code.code}"
    # share_text = f" برای خرید و استفاده از سرویس موقع ثبت نام این کد رو وارد کن: \n\n `{code.code}`  \n\n یا لینک زیر رو بزن که خودش مستقیم وارد کنه "
    share_url = "https://t.me/share/url?" + urlencode({
        "url": referral_link,
        # "text": share_text,

    })
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📤 اشتراک‌گذاری",
                url=share_url,
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ برگشت",
                callback_data="menu:main",
            )
        ],
    ])

    text = (
        f"🎁 کد دعوت تو:\n\n"
        f"`{code.code}`\n\n"
        f"تا حالا {code.used_count} نفر باهاش ثبت‌نام کردن.\n"
        f"باقی‌مانده: {remaining}"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
