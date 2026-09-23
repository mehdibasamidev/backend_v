"""
Posts payment receipts to the Telegram admins, with the approve/reject
buttons that handlers/admin_review.py acts on. Every announcement goes
through here, wherever the receipt came from:

  - the bot's receipt handler calls notify_admins_of_proof() straight away;
  - receipts from the app (REST checkout and renewal) are picked up by
    run_proof_notifier(), which the long-polling container runs next to
    the updater. The web container never talks to Telegram - it leaves
    PaymentProof.admin_notified_at empty, and that column is the outbox.

Webhook mode (apps/bot/views.py) does not run the poller, so while the bot
is served by webhook, receipts from the app are not announced.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.db import close_old_connections
from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError

from apps.bot.services.admin_access import admin_chat_ids
from apps.vpn.models import PaymentProof, PaymentProofKindChoices, PaymentProofSourceChoices

logger = logging.getLogger(__name__)

# Telegram's limits, counted in UTF-16 code units as the Bot API does.
CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096
# Left free for the outcome line admin_review.py appends on approve/reject.
# An edit that would overflow is refused, stranding the admin's copy with
# stale buttons and no result on it.
REVIEW_SUFFIX_RESERVE = 250

_POLL_BATCH_SIZE = 20

_NO_IMAGE_NOTE = "⚠️ تصویر فیش پیوست نشد؛ در پنل ادمین اپلیکیشن قابل مشاهده است."


def review_keyboard(proof_id):
    # admin_review.py splits this callback_data on ":" - keep the shape.
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تایید", callback_data=f"review:approve:{proof_id}"),
        InlineKeyboardButton("❌ رد", callback_data=f"review:reject:{proof_id}"),
    ]])


# ---------------------------------------------------------------------------
# Caption
# ---------------------------------------------------------------------------

def _tg_len(text):
    return len(text.encode("utf-16-le")) // 2


def _cut(text, units):
    """Longest prefix of text within `units`, ending in "…" when cut."""
    if _tg_len(text) <= units:
        return text
    if units <= 1:
        return ""
    kept, used = [], 0
    for char in text:
        size = 2 if ord(char) > 0xFFFF else 1
        if used + size > units - 1:
            break
        kept.append(char)
        used += size
    return "".join(kept) + "…"


def _compose(fixed, free, limit, footer=()):
    """
    Joins the lines, shortening only the `free` (label, text) parts - the
    customer's receipt text and the AI note - until the whole fits `limit`.
    Shortest goes first so a short transaction code stays whole and a long
    AI note takes the cut.
    """
    skeleton = "\n".join([*fixed, *(label for label, _ in free), *footer])
    budget = limit - _tg_len(skeleton)
    values = [""] * len(free)
    order = sorted(range(len(free)), key=lambda i: _tg_len(free[i][1]))
    for rank, index in enumerate(order):
        share = max(budget // (len(order) - rank), 0)
        values[index] = _cut(free[index][1], share)
        budget -= _tg_len(values[index])
    lines = [*fixed, *(label + value for (label, _), value in zip(free, values)), *footer]
    return "\n".join(lines)


def _buyer_label(user, profile):
    name = next(
        (value for value in (user.full_name, user.username, user.phone_number, user.email) if value),
        "—",
    )
    if profile is not None and profile.telegram_username:
        return f"{name} (@{profile.telegram_username})"
    return name


def _amount_label(amount):
    return "—" if amount is None else f"{int(amount):,} تومان"


def _plan_label(subscription):
    plan_name = subscription.plan.name if subscription.plan else "سفارشی"
    volume = "نامحدود" if subscription.is_unlimited_volume else f"{subscription.volume_gb}GB"
    users = "نامحدود" if subscription.is_unlimited_users else subscription.max_concurrent_users
    return f"{plan_name} ({volume} / {subscription.duration_days} روز / کاربر: {users})"


@dataclass
class _Announcement:
    fixed: list
    free: list
    image: bytes | None
    filename: str
    has_receipt_image: bool

    def caption(self):
        return _compose(self.fixed, self.free, CAPTION_LIMIT - REVIEW_SUFFIX_RESERVE)

    def text(self):
        # Text-only is also the fallback when the image could not be sent,
        # and then the admin needs to know there was one.
        footer = [_NO_IMAGE_NOTE] if self.has_receipt_image else []
        return _compose(self.fixed, self.free, MESSAGE_LIMIT - REVIEW_SUFFIX_RESERVE, footer)


def _load_announcement(proof_id):
    """Everything the send needs, read on the sync side. None when there is nothing to announce."""
    proof = (
        PaymentProof.objects
        .select_related("subscription__plan", "subscription__user__telegram_profile")
        .filter(id=proof_id)
        .first()
    )
    # Reviewed already (in the app, between the poller listing it and now):
    # posting it would put live approve/reject buttons on a closed payment.
    if proof is None or proof.admin_notified_at is not None or proof.is_approved is not None:
        return None

    subscription = proof.subscription
    buyer = subscription.user
    profile = getattr(buyer, "telegram_profile", None)
    is_renewal = proof.kind == PaymentProofKindChoices.RENEWAL

    fixed = [
        "🔁 فیش تمدید" if is_renewal else "🧾 فیش پرداخت جدید",
        # Recorded on the proof, not guessed from the profile: a buyer who
        # connected their app account to Telegram has a profile too.
        "منبع: 🤖 ربات تلگرام" if proof.source == PaymentProofSourceChoices.BOT else "منبع: 📱 اپلیکیشن",
        f"کاربر: {_buyer_label(buyer, profile)}",
        f"مبلغ: {_amount_label(proof.amount)}",
        f"پلن: {_plan_label(subscription)}",
    ]
    if is_renewal:
        extras = []
        if proof.extra_days:
            extras.append(f"+{proof.extra_days} روز")
        if proof.extra_gb:
            extras.append(f"+{proof.extra_gb} GB")
        if extras:
            fixed.append(f"تمدید: {' / '.join(extras)}")

    free = []
    if proof.receipt_text:
        free.append(("متن/کد پیگیری: ", proof.receipt_text))
    if proof.ai_checked:
        verdict = f"نظر AI: {proof.ai_verdict}"
        free.append((f"{verdict} - ", proof.ai_notes) if proof.ai_notes else (verdict, ""))

    image = None
    filename = ""
    if proof.receipt_image:
        filename = os.path.basename(proof.receipt_image.name)
        try:
            proof.receipt_image.open("rb")
            try:
                image = proof.receipt_image.read()
            finally:
                proof.receipt_image.close()
        except Exception:
            # Storage trouble must not hold the receipt back: it goes out
            # as text, and the admin opens the image in the app.
            logger.exception("Could not read the receipt image of payment proof %s", proof_id)

    return _Announcement(
        fixed=fixed,
        free=free,
        image=image,
        filename=filename,
        has_receipt_image=bool(proof.receipt_image),
    )


# ---------------------------------------------------------------------------
# Claim / release
# ---------------------------------------------------------------------------

def _claim(proof_id):
    """
    A conditional UPDATE, so exactly one caller wins: the bot handler's
    immediate send and the poller run in the same process, and a second
    bot container would poll the same table. Returns the timestamp written,
    or None when someone else already has it or the proof has been
    reviewed in the meantime.

    The cost of claiming before sending: a process killed outright (SIGKILL,
    a crash) between the two leaves the proof marked announced without a
    message. It is still in the app's review queue. Cancellation - docker
    stop, via telegram_polling's SIGTERM handler - releases the claim; see
    notify_admins_of_proof.
    """
    claimed_at = timezone.now()
    won = PaymentProof.objects.filter(
        id=proof_id, admin_notified_at__isnull=True, is_approved__isnull=True,
    ).update(admin_notified_at=claimed_at)
    return claimed_at if won == 1 else None


def _release(proof_id, claimed_at):
    # Matches our own timestamp so a newer claim is never undone.
    PaymentProof.objects.filter(
        id=proof_id, admin_notified_at=claimed_at,
    ).update(admin_notified_at=None)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def _is_transient(exc):
    # In PTB 22 BadRequest and TimedOut both subclass NetworkError. A
    # BadRequest is Telegram refusing this exact request and it would
    # refuse a retry too; the rest of NetworkError (TimedOut included) is
    # the connection. RetryAfter is flood control. Forbidden - blocked, or
    # removed from the group - is permanent like BadRequest.
    #
    # A TimedOut upload may still have been delivered, so a retry can post
    # a duplicate; that beats an admin never seeing the receipt.
    if isinstance(exc, RetryAfter):
        return True
    return isinstance(exc, NetworkError) and not isinstance(exc, BadRequest)


# Fragments of Telegram's BadRequest text that blame the file itself (HEIC
# from an iPhone, a huge or empty image, a bad file_id), as opposed to the
# chat refusing it. Matched lowercase. "photo_", not "photo": a group where
# the bot lacks the media permission answers "not enough rights to send
# photos to the chat", and that says nothing about the other chats.
_IMAGE_REFUSAL_MARKERS = (
    "image", "photo_", "file", "dimension", "too big", "too large",
    "media_empty", "document_invalid",
)


def _blames_the_image(exc):
    message = (exc.message or "").lower()
    return any(marker in message for marker in _IMAGE_REFUSAL_MARKERS)


class _Sender:
    """
    Sends one announcement to several chats, degrading photo -> document
    -> text when Telegram will not take the image, and reusing the file_id
    it hands back so later chats skip the upload.
    """

    def __init__(self, bot, proof_id, announcement):
        self.bot = bot
        self.announcement = announcement
        self.keyboard = review_keyboard(proof_id)
        if announcement.image is not None:
            self.modes = ["photo", "document", "text"]
        else:
            self.modes = ["text"]
        self.photo = announcement.image
        self.document = announcement.image

    async def _send(self, chat_id, mode):
        if mode == "photo":
            message = await self.bot.send_photo(
                chat_id=chat_id, photo=self.photo,
                caption=self.announcement.caption(), reply_markup=self.keyboard,
            )
            self.photo = message.photo[-1].file_id
        elif mode == "document":
            message = await self.bot.send_document(
                chat_id=chat_id, document=self.document, filename=self.announcement.filename,
                caption=self.announcement.caption(), reply_markup=self.keyboard,
            )
            self.document = message.document.file_id
        else:
            await self.bot.send_message(
                chat_id=chat_id, text=self.announcement.text(), reply_markup=self.keyboard,
            )

    async def send(self, chat_id):
        """Raises the last error when no form got through."""
        last_error = None
        for mode in list(self.modes):
            try:
                await self._send(chat_id, mode)
            except BadRequest as exc:
                last_error = exc
                if mode != "text" and _blames_the_image(exc):
                    # Every chat would refuse this file the same way: the
                    # remaining chats skip this form. Any other refusal
                    # ("chat not found", no media rights in the group) is
                    # this chat's alone, and the next admin still gets the
                    # image.
                    self.modes.remove(mode)
                continue
            return
        raise last_error


async def notify_admins_of_proof(bot, proof_id) -> bool:
    """
    Posts one receipt to every admin chat. True when at least one chat got
    it. Safe to call more than once for the same proof - only the caller
    that claims it sends.
    """
    chat_ids = admin_chat_ids()
    if not chat_ids:
        logger.warning(
            "Payment proof %s not announced: neither TELEGRAM_ADMIN_GROUP_CHAT_ID "
            "nor TELEGRAM_ADMIN_USER_IDS is set",
            proof_id,
        )
        return False

    # Read before claiming, so a failure here leaves the proof unclaimed
    # for the next poll instead of claimed and never sent.
    announcement = await sync_to_async(_load_announcement)(proof_id)
    if announcement is None:
        return False

    claimed_at = await sync_to_async(_claim)(proof_id)
    if claimed_at is None:
        return False

    sender = _Sender(bot, proof_id, announcement)
    delivered = 0
    transient_chats = []
    permanent_chats = []
    # One failing chat must not swallow the rest: an admin who never pressed
    # /start makes every send to them fail, and everyone else still needs it.
    try:
        for chat_id in chat_ids:
            try:
                await sender.send(chat_id)
                delivered += 1
            except Exception as exc:
                if _is_transient(exc):
                    transient_chats.append(chat_id)
                else:
                    permanent_chats.append(chat_id)
                if isinstance(exc, TelegramError):
                    logger.warning("Could not deliver payment proof %s to chat %s: %r", proof_id, chat_id, exc)
                else:
                    logger.exception("Could not deliver payment proof %s to chat %s", proof_id, chat_id)
    except BaseException:
        # Cancelled mid-send - docker stop reaches here through
        # telegram_polling's SIGTERM handler. CancelledError is not an
        # Exception, so the loop above lets it through; without this the
        # claim would stay with nothing posted and no poll would retry it.
        # Shielded so the release itself cannot be cancelled halfway.
        if not delivered:
            await asyncio.shield(sync_to_async(_release)(proof_id, claimed_at))
            logger.warning("Payment proof %s interrupted before any admin got it; released for the next poll", proof_id)
        raise

    if delivered:
        return True

    if transient_chats:
        await sync_to_async(_release)(proof_id, claimed_at)
        logger.warning("Payment proof %s reached no admin chat; released for the next poll", proof_id)
        return False

    # Nothing but refusals. A retry would be refused the same way every ten
    # seconds, so the claim stays and this needs a human.
    logger.error(
        "Payment proof %s could not be posted to any admin chat (%s). Check the "
        "chat ids and that every admin has pressed /start; it is still in the "
        "app's review queue.",
        proof_id, ", ".join(permanent_chats),
    )
    return False


# ---------------------------------------------------------------------------
# Poller
# ---------------------------------------------------------------------------

def _due_proof_ids(grace):
    # This process never runs Django's request cycle, whose signals are
    # what normally drop dead database connections. Without this, a
    # Postgres restart leaves the bot on a broken connection until the
    # container itself restarts.
    close_old_connections()
    cutoff = timezone.now() - timedelta(seconds=grace)
    return list(
        PaymentProof.objects.filter(
            is_approved__isnull=True,
            admin_notified_at__isnull=True,
            created_at__lte=cutoff,
        )
        .order_by("created_at")
        .values_list("id", flat=True)[:_POLL_BATCH_SIZE]
    )


async def run_proof_notifier(bot, *, interval=10, grace=15):
    """
    Announces every pending, unannounced receipt, forever.

    `grace` holds a new proof back for a few seconds: the bot handler's own
    immediate send and the synchronous AI pre-check that runs right after
    a proof is created (REST view and bot alike) normally finish inside it,
    so the poller rarely races the handler and its caption carries the AI
    note.
    """
    while True:
        try:
            if admin_chat_ids():
                for proof_id in await sync_to_async(_due_proof_ids)(grace):
                    try:
                        await notify_admins_of_proof(bot, proof_id)
                    except Exception:
                        # One bad row must not starve the ones behind it.
                        logger.exception("Announcing payment proof %s failed", proof_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Payment proof notifier tick failed; retrying in %ss", interval)
        await asyncio.sleep(interval)
