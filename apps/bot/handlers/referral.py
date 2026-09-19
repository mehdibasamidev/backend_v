from asgiref.sync import sync_to_async
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

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
    """/mycode - the caller's own invite code, with a share button."""
    profile = await sync_to_async(get_or_create_telegram_user)(
        update.effective_user
    )
    if profile is None:
        await update.message.reply_text("اول باید ثبت‌نام کنی. /start رو بزن.")
        return

    try:
        code = await sync_to_async(get_or_create_personal_code)(profile.user)
    except AppException as exc:
        await update.message.reply_text(exc.message)
        return

    remaining = (
        "نامحدود" if code.max_uses == 0 else f"{code.remaining_uses} بار"
    )

    share_text = f" موقع ثبت نام این کد رو وارد کن: \n {code.code}  \n یا \n https://t.me/{context.bot.username}?start={code.code}"

    await update.message.reply_text(
        f"🎁 کد دعوت تو:\n\n`{code.code}`\n\n"
        f"تا حالا {code.used_count} نفر باهاش ثبت‌نام کردن.\n"
        f"باقی‌مانده: {remaining}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "📤 اشتراک‌گذاری",
                # Telegram's own share sheet - no deep link needed, and it
                # works from inside the chat.
                url=f"https://t.me/share/url?url={share_text}",
                copy_text=share_text
                )
        ]]),
    )
