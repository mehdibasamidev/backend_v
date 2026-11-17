from asgiref.sync import sync_to_async
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from urllib.parse import urlencode
from apps.bot.services.formatting import fa_decimal_amount
from apps.bot.services.keyboards import main_menu_keyboard, main_reply_keyboard
from apps.bot.services.registration import (
    get_or_create_telegram_user,
    needs_referral_code,
    submit_referral_code,
)
from apps.referral.models import ReferralSettings, RewardKind
from apps.referral.services.redemption import get_or_create_personal_code
from config.utils.exceptions import AppException


def _referral_reward_offer():
    policy = ReferralSettings.get_solo()
    if not policy.personal_codes_enabled or policy.reward_value <= 0:
        return ""
    if policy.reward_kind == RewardKind.WALLET:
        return f"{fa_decimal_amount(policy.reward_value)} تومان"
    if policy.reward_kind == RewardKind.DISCOUNT:
        return f"{fa_decimal_amount(policy.reward_value)}٪ تخفیف"
    return ""


async def try_handle_referral_code(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    """
    Consumes an invite code from either:
    - a plain text message
    - a Telegram deep-link: /start CODE

    Returns True when the referral flow handled the message.
    """
    telegram_user = update.effective_user

    if not await sync_to_async(needs_referral_code)(telegram_user.id):
        return False

    # Deep-link: /start CENVPGKY
    if context.args:
        raw_code = context.args[0].strip()
    else:
        raw_code = (update.message.text or "").strip()

    ok, result = await sync_to_async(submit_referral_code)(
        telegram_user,
        raw_code,
    )

    if not ok:
        await update.message.reply_text(
            f"❌ {result}\n\nیه کد دیگه امتحان کن."
        )
        return True

    await update.message.reply_text(
        "✅ خوش اومدی! حسابت ساخته شد.",
        reply_markup=main_reply_keyboard() if update.effective_chat.type == "private" else main_menu_keyboard(),
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
    offer = await sync_to_async(_referral_reward_offer)()
    if offer:
        text += (
            "\n\nاگر دوستت با کدت ثبت‌نام کند، بعد از تأیید اولین خرید پرداختی‌اش در اپ، "
            f"{offer} پاداش می‌گیری."
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
