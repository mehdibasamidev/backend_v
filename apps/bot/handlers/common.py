from asgiref.sync import sync_to_async
from telegram import Update
from telegram.ext import ContextTypes
from apps.bot.handlers.referral import try_handle_referral_code
from apps.bot.handlers.telegram_auth import try_handle_auth_start
from apps.bot.services.keyboards import main_menu_keyboard
from apps.bot.services.registration import (
    begin_referral_prompt,
    get_or_create_telegram_user,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Deep links from the app ("Login with Telegram" / "Connect Telegram")
    # go first: someone still owing an invite code who opens one must get
    # that flow, not have its payload rejected as a bad invite code.
    if await try_handle_auth_start(update, context):
        return

    if context.args:

        handled = await try_handle_referral_code(update, context)

        if handled:

            return
    profile = await sync_to_async(get_or_create_telegram_user)(

        update.effective_user

    )

    # None means invite codes are required and this person doesn't have an
    # account yet. Nothing is created until they send a valid one, so the
    # menu is deliberately withheld - showing it would offer plans they
    # can't actually buy.
    if profile is None:
        await sync_to_async(begin_referral_prompt)(update.effective_user)
        if context.args:
            # An invite link (t.me/<bot>?start=<CODE>) carries the code
            # itself, and the invitee never saw it. Redeem it on this first
            # press; they are only asked to type one if it is invalid.
            await try_handle_referral_code(update, context)
            return
        await update.message.reply_text(
            "سلام 👋\n\n"
            "برای ثبت‌نام به یک کد معرف نیاز داری.\n"
            "کدت رو همین‌جا برام بفرست تا حسابت ساخته بشه."
        )
        return

    await update.message.reply_text(
        "سلام 👋\nاز این‌جا می‌تونی سرویس VPN بخری، پلن سفارشی بسازی یا سرویس‌های فعالت رو مدیریت کنی.",
        reply_markup=main_menu_keyboard(),
    )


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("منوی اصلی:", reply_markup=main_menu_keyboard())


async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Used for the +/- stepper's middle "label" button - it isn't meant to do anything.
    await update.callback_query.answer()
