from asgiref.sync import sync_to_async
from django.conf import settings
from telegram import Update
from telegram.ext import ContextTypes

from apps.vpn.models import PaymentProof
from apps.vpn.models import PaymentProofKindChoices
from apps.vpn.services.review import approve_payment_proof, reject_payment_proof


async def review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if str(query.message.chat_id) != str(settings.TELEGRAM_ADMIN_GROUP_CHAT_ID):
        await query.answer("این دکمه فقط توی گروه ادمین‌ها کار می‌کنه.", show_alert=True)
        return

    member = await context.bot.get_chat_member(settings.TELEGRAM_ADMIN_GROUP_CHAT_ID, query.from_user.id)
    if member.status not in ("administrator", "creator"):
        await query.answer("فقط ادمین‌های گروه می‌تونن تایید/رد کنن.", show_alert=True)
        return

    await query.answer()
    _, action, proof_id = query.data.split(":")

    def _load():
        return PaymentProof.objects.select_related("subscription", "subscription__user").get(id=proof_id)

    try:
        proof = await sync_to_async(_load)()
    except PaymentProof.DoesNotExist:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if proof.is_approved is not None:
        await query.answer("این فیش قبلاً بررسی شده.", show_alert=True)
        return

    def _apply():
        if action == "approve":
            return approve_payment_proof(proof)
        return reject_payment_proof(proof)

    subscription = await sync_to_async(_apply)()

    is_renewal = proof.kind == PaymentProofKindChoices.RENEWAL
    if action == "approve":
        result_text = "✅ تایید شد و روی پنل اعمال شد."
        if is_renewal:
            buyer_text = (
                "🎉 تمدید سرویست تایید شد!\n"
                f"مدت جدید: {subscription.remaining_days} روز باقی‌مانده"
            )
        else:
            buyer_text = (
                "🎉 پرداختت تایید شد و سرویست فعال شد!\n"
                f"لینک ساب: {subscription.subscription_link}\n"
                f"مدت: {subscription.duration_days} روز"
            )
    else:
        result_text = "❌ رد شد."
        buyer_text = "متاسفانه فیش پرداختت تایید نشد. لطفاً با پشتیبانی در تماس باش."

    try:
        new_caption = f"{query.message.caption}\n\n{result_text}"
        await context.bot.edit_message_caption(
            chat_id=query.message.chat_id, message_id=query.message.message_id, caption=new_caption,
        )
    except Exception:
        new_text = f"{query.message.text}\n\n{result_text}"
        await context.bot.edit_message_text(
            chat_id=query.message.chat_id, message_id=query.message.message_id, text=new_text,
        )

    telegram_profile = await sync_to_async(lambda: getattr(subscription.user, "telegram_profile", None))()
    if telegram_profile:
        await context.bot.send_message(chat_id=telegram_profile.telegram_user_id, text=buyer_text)
