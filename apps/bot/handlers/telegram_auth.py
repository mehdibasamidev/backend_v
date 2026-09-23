"""
Bot half of "Login with Telegram" and "Connect Telegram": /start with a
login_/link_ payload shows the confirmation with its code buttons, and a
button press settles the request. The rules are in
apps/bot/services/telegram_auth.py.
"""

import logging

from asgiref.sync import sync_to_async
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from apps.bot.models import TelegramAuthPurpose, TelegramAuthStatus
from apps.bot.services.telegram_auth import (
    CANCEL_CHOICE,
    TelegramAuthExpired,
    code_choices,
    confirm_request,
    confirmation_text,
    open_request,
    reason_fa,
)
from config.utils.exceptions import AppException, ForbiddenException

logger = logging.getLogger(__name__)

AUTH_START_PREFIXES = ("login_", "link_")
# bot_app.py routes on this prefix: r"^tga:".
CALLBACK_PREFIX = "tga"
# Telegram refuses a button whose callback_data is longer than this, in bytes.
_CALLBACK_DATA_LIMIT = 64

_START_AGAIN = "از داخل اپلیکیشن دوباره شروع کن."


def _callback_data(request_id, choice):
    data = f"{CALLBACK_PREFIX}:{request_id}:{choice}"
    # "tga:" + a 36-character uuid + ":" + 4 digits is 45 bytes. Checked
    # anyway: over the limit Telegram refuses the whole message, and the
    # person is left with a link that seems to do nothing.
    if len(data.encode()) > _CALLBACK_DATA_LIMIT:
        raise ValueError(f"callback_data {data!r} is over Telegram's {_CALLBACK_DATA_LIMIT} bytes")
    return data


def _code_keyboard(request_id, choices):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(code, callback_data=_callback_data(request_id, code))
            for code in choices
        ],
        [InlineKeyboardButton("❌ انصراف", callback_data=_callback_data(request_id, CANCEL_CHOICE))],
    ])


def _open(payload, telegram_user):
    """Sync half of the /start: everything that touches the ORM."""
    request, reason = open_request(payload, telegram_user)
    if request is None:
        return None, reason, []
    return request, confirmation_text(request), code_choices(request)


async def try_handle_auth_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handles /start login_<token> and /start link_<token>. Returns True for
    any payload with those prefixes, valid or not, so it is never read as
    an invite code instead.
    """
    if not context.args:
        return False
    payload = context.args[0].strip()
    if not payload.startswith(AUTH_START_PREFIXES):
        return False

    request, text, choices = await sync_to_async(_open)(payload, update.effective_user)
    if request is None:
        await update.message.reply_text(f"❌ {text}\n\n{_START_AGAIN}")
        return True

    await update.message.reply_text(text, reply_markup=_code_keyboard(request.id, choices))
    return True


def outcome_text(request):
    if request.status == TelegramAuthStatus.CONFIRMED:
        if request.purpose == TelegramAuthPurpose.LOGIN:
            return "✅ ورودت تأیید شد.\nحالا به اپلیکیشن برگرد؛ خودش واردت می‌کنه."
        text = (
            "✅ تلگرامت به حساب اپلیکیشن وصل شد.\n"
            "از این به بعد ربات و اپلیکیشن یک حساب هستن."
        )
        if request.moved_subscriptions:
            text += f"\n{request.moved_subscriptions} سرویس از ربات به این حساب منتقل شد."
        return text
    return f"❌ {reason_fa(request) or 'درخواست انجام نشد.'}\n\n{_START_AGAIN}"


async def _replace_message(query, text):
    try:
        # An edit without reply_markup also removes the buttons, so the
        # outcome can't be pressed again.
        await query.edit_message_text(text)
    except BadRequest:
        logger.warning("Could not edit the Telegram auth message for %s", query.data, exc_info=True)


async def _drop_buttons(query):
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except BadRequest:
        # "Message is not modified": a double tap already removed them.
        pass


async def handle_auth_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    A code or cancel button. Every path answers the query exactly once -
    Telegram accepts only one answer, and the button keeps spinning until
    it gets it.
    """
    query = update.callback_query
    parts = (query.data or "").split(":")
    if len(parts) != 3:
        await query.answer()
        return
    _, request_id, choice = parts

    try:
        request = await sync_to_async(confirm_request)(request_id, query.from_user, choice)
    except ForbiddenException as exc:
        # Not the person who opened the link. Their buttons stay put.
        await query.answer(exc.message, show_alert=True)
        return
    except TelegramAuthExpired as exc:
        await query.answer()
        await _replace_message(query, f"⌛ {exc.message}\n\n{_START_AGAIN}")
        return
    except AppException as exc:
        # Already settled - a double tap, or a second copy of the message
        # from opening the link twice. The first outcome's text stays;
        # only the stale buttons go.
        await query.answer(exc.message, show_alert=True)
        await _drop_buttons(query)
        return
    except Exception:
        # Nothing was written (the confirmation is one transaction), so the
        # buttons stay and a retry is safe.
        logger.exception("Telegram auth confirmation failed for %s", query.data)
        await query.answer("مشکلی پیش اومد، دوباره امتحان کن.", show_alert=True)
        return

    await query.answer()
    await _replace_message(query, outcome_text(request))
