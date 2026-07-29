from asgiref.sync import sync_to_async
from telegram import Update
from telegram.ext import ContextTypes

from apps.bot.services.registration import get_or_create_telegram_user
from apps.bot.services.keyboards import main_menu_keyboard


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await sync_to_async(get_or_create_telegram_user)(update.effective_user)
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
