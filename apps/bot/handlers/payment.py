from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.files.base import ContentFile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from apps.vpn.services.ai_receipt import analyze_payment_receipt
from apps.vpn.services.checkout import create_paid_order
from apps.bot.services.registration import get_or_create_telegram_user


def _parse_awaiting(action: str):
    """
    'checkout:fixed:<plan_id>'            -> dict for a fixed plan
    'checkout:custom:<gb>:<days>:<users>' -> dict for a custom plan
    """
    parts = action.split(":")
    if len(parts) < 3 or parts[0] != "checkout":
        return None
    if parts[1] == "fixed":
        return {"plan_id": parts[2]}
    if parts[1] == "custom" and len(parts) == 5:
        return {
            "volume_gb": int(parts[2]),
            "duration_days": int(parts[3]),
            "max_concurrent_users": int(parts[4]),
        }
    return None


async def receive_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Generic photo/text handler. Only acts when the user is mid-checkout;
    otherwise this is just a normal chat message and we ignore it.
    """
    def _load_state():
        profile = get_or_create_telegram_user(update.effective_user)
        order = _parse_awaiting(profile.awaiting_action or "")
        return profile, order

    profile, order = await sync_to_async(_load_state)()
    if order is None:
        return  # not checking out right now - ignore

    receipt_text = ""
    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.text:
        receipt_text = update.message.text.strip()
    else:
        await update.message.reply_text("لطفاً یه عکس یا متن بفرست.")
        return

    image_bytes = None
    if file_id:
        tg_file = await context.bot.get_file(file_id)
        image_bytes = bytes(await tg_file.download_as_bytearray())

    def _create():
        image = ContentFile(image_bytes, name=f"{file_id}.jpg") if image_bytes else None
        subscription, proof = create_paid_order(
            user=profile.user,
            receipt_image=image,
            receipt_text=receipt_text,
            **order,
        )
        profile.clear_awaiting_action()
        return subscription, proof

    try:
        subscription, proof = await sync_to_async(_create)()
    except Exception as e:
        await update.message.reply_text(f"⚠️ ثبت سفارش انجام نشد: {e}")
        return

    try:
        await sync_to_async(analyze_payment_receipt)(proof)
    except Exception:
        pass  # AI review is best-effort only - never block the flow on it

    await update.message.reply_text("فیش دریافت شد ✅ به‌محض تایید ادمین، سرویس فعال میشه.")
    await _notify_admin_group(context, proof)


async def _notify_admin_group(context: ContextTypes.DEFAULT_TYPE, proof):
    def _build_caption():
        proof.refresh_from_db()
        sub = proof.subscription
        buyer = sub.user
        volume_text = "نامحدود" if sub.is_unlimited_volume else f"{sub.volume_gb}GB"
        lines = [
            "🧾 فیش پرداخت جدید",
            f"کاربر: {buyer.full_name or buyer.email}",
            f"مبلغ: {proof.amount} تومان",
            f"پلن: {sub.plan.name if sub.plan else 'سفارشی'} ({volume_text} / {sub.duration_days}d)",
        ]
        if proof.receipt_text:
            lines.append(f"متن/کد پیگیری: {proof.receipt_text}")
        if proof.ai_checked:
            lines.append(f"نظر AI: {proof.ai_verdict} - {proof.ai_notes}")
        return "\n".join(lines), bool(proof.receipt_image)

    caption, has_image = await sync_to_async(_build_caption)()
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تایید", callback_data=f"review:approve:{proof.id}"),
        InlineKeyboardButton("❌ رد", callback_data=f"review:reject:{proof.id}"),
    ]])

    if has_image:
        image_bytes = await sync_to_async(lambda: proof.receipt_image.read())()
        await context.bot.send_photo(
            chat_id=settings.TELEGRAM_ADMIN_GROUP_CHAT_ID,
            photo=image_bytes,
            caption=caption,
            reply_markup=keyboard,
        )
    else:
        await context.bot.send_message(
            chat_id=settings.TELEGRAM_ADMIN_GROUP_CHAT_ID,
            text=caption,
            reply_markup=keyboard,
        )
