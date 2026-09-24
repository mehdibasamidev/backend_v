"""
"Login with Telegram" and "Connect Telegram".

The app starts a TelegramAuthRequest and shows its 4-digit code; the person
opens the deep link in Telegram, and the bot confirms once they pick that
code among two decoys. The app polls until the request is done.

Called by the REST views (apps/bot/api_views.py) and by the bot handlers
(apps/bot/handlers/telegram_auth.py, through sync_to_async). Everything here
is synchronous ORM work.

Two audiences, two languages: failure_reason and the dicts returned to the
app are English, like every other API message; the texts the bot shows are
Persian.
"""

import hashlib
import hmac
import logging
import random
import re
import secrets
import uuid

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.account.serializers.profile import UserInfoSerializer
from apps.account.services.session import auth_payload
from apps.bot.models import (
    TelegramAuthPurpose,
    TelegramAuthRequest,
    TelegramAuthStatus,
    TelegramProfile,
)
from apps.bot.services.account_merge import (
    is_bot_only_account,
    merge_bot_account_into,
)
from apps.bot.services.bot_settings import effective_bot_username
from apps.bot.services.registration import get_or_create_telegram_user
from config.utils.exceptions import (
    AppException,
    BadRequestException,
    ForbiddenException,
    NotFoundException,
)

User = get_user_model()
logger = logging.getLogger("apps")

# Telegram's rule for a /start payload: 1-64 characters of A-Z a-z 0-9 _ -.
# Outside it Telegram drops the payload and the person lands on a bare
# /start, with nothing to say why the link did nothing.
_START_PAYLOAD_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# 24 random bytes are 32 URL-safe characters: "login_" + 32 = 38.
_START_TOKEN_BYTES = 24
_POLL_SECRET_BYTES = 32

# The cancel button's value in place of a code.
CANCEL_CHOICE = "x"
_CHOICE_COUNT = 3

# ---------------------------------------------------------------------------
# Reasons
# ---------------------------------------------------------------------------

# failure_reason values, which the app shows. The bot's words for each are
# in _REASON_FA, looked up by the same string.
REASON_CANCELLED = "Cancelled in Telegram."
REASON_WRONG_CODE = (
    "The code picked in Telegram didn't match the one shown in the app, "
    "so the request was cancelled."
)
REASON_REPLACED = "A newer request replaced this one."
REASON_INVITE_REQUIRED = (
    "An invite code is required to create an account. Open the bot, press "
    "/start and send your invite code, then try again."
)
REASON_ACCOUNT_DISABLED = "This account has been disabled."
REASON_TELEGRAM_TAKEN = (
    "This Telegram account is already connected to another app account."
)
REASON_TELEGRAM_ACCOUNT_DISABLED = (
    "The bot account of this Telegram has been disabled."
)
REASON_ALREADY_CONNECTED = (
    "This account is already connected to a Telegram account."
)
REASON_RETRY = "Your Telegram account changed while connecting. Start again."

_REASON_FA = {
    REASON_CANCELLED: "درخواست لغو شد.",
    REASON_WRONG_CODE: "کدی که زدی با کد داخل اپلیکیشن یکی نبود، برای همین درخواست لغو شد.",
    REASON_REPLACED: "یه درخواست جدیدتر جای این یکی رو گرفته.",
    REASON_INVITE_REQUIRED: (
        "هنوز توی ربات حساب نداری و برای ساختنش کد دعوت لازمه.\n"
        "اول /start رو بزن و کد دعوتت رو بفرست."
    ),
    REASON_ACCOUNT_DISABLED: "این حساب غیرفعال شده.",
    REASON_TELEGRAM_TAKEN: "این حساب تلگرام قبلاً به یه حساب دیگهٔ اپلیکیشن وصل شده.",
    REASON_TELEGRAM_ACCOUNT_DISABLED: "حساب ربات این تلگرام غیرفعال شده.",
    REASON_ALREADY_CONNECTED: "این حساب اپلیکیشن قبلاً به یه حساب تلگرام دیگه وصل شده.",
    REASON_RETRY: "وسط کار، حساب تلگرامت توی ربات تغییر کرد.",
}

_INVALID_LINK_FA = "این لینک معتبر نیست."
_NOT_FOUND_FA = "این درخواست پیدا نشد."


class TelegramAuthExpired(BadRequestException):
    """confirm_request on a request past its TTL. Nothing is written."""


def reason_fa(request):
    """The bot's wording of request.failure_reason, or "" when there is none."""
    return _REASON_FA.get(request.failure_reason, "")


def _closed_text_fa(request):
    """Why a request can no longer be opened or confirmed, or "" if it still can."""
    status = request.effective_status
    if status == "expired":
        return "زمان این درخواست تموم شده."
    if status in (TelegramAuthStatus.CONFIRMED, TelegramAuthStatus.USED):
        return "این درخواست قبلاً تأیید شده."
    if status == TelegramAuthStatus.CANCELLED:
        return "این درخواست لغو شده."
    if status == TelegramAuthStatus.FAILED:
        return reason_fa(request) or "این درخواست انجام نشد."
    return ""


def _app_message(request, status):
    if status == "expired":
        return "This request expired. Start again."
    if status == TelegramAuthStatus.USED:
        return "This sign-in was already completed. Start again to sign in here."
    if status in (TelegramAuthStatus.CANCELLED, TelegramAuthStatus.FAILED):
        return request.failure_reason or None
    return None


# ---------------------------------------------------------------------------
# Starting (app side)
# ---------------------------------------------------------------------------

def _require_bot_username():
    """The username for the deep link: the admin's, else the one the bot container recorded."""
    username = effective_bot_username()
    if not username:
        raise BadRequestException(
            "Telegram sign-in isn't set up on the server yet. An admin needs to "
            "set the bot's username in the admin panel (Settings tab), or start "
            "the bot container so it records it."
        )
    return username


def _hash_secret(secret):
    # sha256 rather than make_password: the poll secret and the start token
    # are 32 and 24 random bytes, so there is nothing to brute-force, and a
    # deliberately slow hash would only slow down every poll and /start.
    return hashlib.sha256(secret.encode()).hexdigest()


def _purge_stale():
    # Housekeeping on the write path instead of a cron job: every start
    # clears out what nobody can use any more. A confirmed link and a used
    # login stay - they are the only record of services moving between
    # accounts and of who signed in as whom. A login confirmed but never
    # collected goes: no session was handed out, and keeping it would keep
    # its session collectible for good.
    kept = (
        Q(purpose=TelegramAuthPurpose.LINK, status=TelegramAuthStatus.CONFIRMED)
        | Q(purpose=TelegramAuthPurpose.LOGIN, status=TelegramAuthStatus.USED)
    )
    TelegramAuthRequest.objects.filter(
        created_at__lt=timezone.now() - TelegramAuthRequest.RETENTION
    ).exclude(kept).delete()


def _create(purpose, bot_username, *, user=None, poll_secret=""):
    """
    Returns (request, deep_link). The start token is only ever in the deep
    link; the row keeps its hash.
    """
    start_token = secrets.token_urlsafe(_START_TOKEN_BYTES)
    # Built before the insert, so a token that breaks Telegram's rules
    # writes nothing.
    deep_link = build_deep_link(purpose, start_token, bot_username=bot_username)
    request = TelegramAuthRequest.objects.create(
        purpose=purpose,
        start_token_hash=_hash_secret(start_token),
        poll_secret_hash=_hash_secret(poll_secret) if poll_secret else "",
        code=f"{secrets.randbelow(10_000):04d}",
        user=user,
        expires_at=timezone.now() + TelegramAuthRequest.TTL,
    )
    return request, deep_link


def start_login_request():
    """
    Returns (request, deep_link, poll_secret). The deep link and the secret
    are only ever in this return value; the row keeps their hashes. Whoever
    holds the secret collects the session, so it must reach nobody but the
    app that asked.
    """
    bot_username = _require_bot_username()
    _purge_stale()
    poll_secret = secrets.token_urlsafe(_POLL_SECRET_BYTES)
    request, deep_link = _create(
        TelegramAuthPurpose.LOGIN, bot_username, poll_secret=poll_secret,
    )
    return request, deep_link, poll_secret


@transaction.atomic
def start_link_request(user):
    """Returns (request, deep_link). The deep link is only ever in this return value."""
    bot_username = _require_bot_username()
    if TelegramProfile.objects.filter(user=user).exists():
        raise BadRequestException("This account is already connected to Telegram.")
    _purge_stale()
    # An older link from the same account would otherwise stay confirmable
    # in Telegram for the rest of its ten minutes, with a code the app no
    # longer shows.
    TelegramAuthRequest.objects.filter(
        user=user,
        purpose=TelegramAuthPurpose.LINK,
        status=TelegramAuthStatus.PENDING,
    ).update(status=TelegramAuthStatus.CANCELLED, failure_reason=REASON_REPLACED)
    return _create(TelegramAuthPurpose.LINK, bot_username, user=user)


def start_payload(purpose, start_token):
    payload = f"{purpose}_{start_token}"
    # Not an assert: those vanish under python -O, and this is exactly the
    # kind of limit someone lengthening the token would trip over.
    if not _START_PAYLOAD_RE.match(payload):
        raise ValueError(f"Deep-link payload {payload!r} breaks Telegram's /start rules")
    return payload


def build_deep_link(purpose, start_token, *, bot_username):
    return f"https://t.me/{bot_username}?start={start_payload(purpose, start_token)}"


# ---------------------------------------------------------------------------
# Opening (bot side, /start <payload>)
# ---------------------------------------------------------------------------

def open_request(payload, telegram_user):
    """
    Validates a deep-link payload and records who opened it.

    Returns (request, "") when the confirmation can be shown, or
    (None, Persian reason) when it can't. The first Telegram account to
    open a link owns it: only it may press the code buttons.
    """
    purpose, _, token = (payload or "").partition("_")
    if purpose not in TelegramAuthPurpose.values or not token:
        return None, _INVALID_LINK_FA

    with transaction.atomic():
        request = (
            TelegramAuthRequest.objects.select_for_update()
            .filter(purpose=purpose, start_token_hash=_hash_secret(token))
            .first()
        )
        if request is None:
            return None, _INVALID_LINK_FA

        closed = _closed_text_fa(request)
        if closed:
            return None, closed

        if request.opened_by_telegram_id is None:
            request.opened_by_telegram_id = telegram_user.id
            request.save(update_fields=["opened_by_telegram_id"])
        elif request.opened_by_telegram_id != telegram_user.id:
            return None, "این لینک قبلاً توی یه حساب تلگرام دیگه باز شده."

    return request, ""


def mask_account(user):
    """
    Enough of the app account for its owner to recognise it, and no more -
    or "" when it has nothing proven to show.

    Only what the account holder proved with a code (or Google): the phone
    when is_phone_verified, the email when is_email_verified. Whoever
    started the link chooses everything else, so it can't be trusted to
    tell the person in Telegram whose account this is: an unverified phone
    or email can be the victim's own, and a username or full name can read
    as their Telegram handle, a phone number or "your account".
    """
    if user.phone_number and user.is_phone_verified:
        return _mask_phone(user.phone_number)
    if user.email and user.is_email_verified:
        local, _, domain = user.email.partition("@")
        return f"{local[:1]}***@{domain}"
    return ""


def _mask_phone(phone):
    # Stored as E.164 (+989121234521); shown the way it is written in Iran.
    local = "0" + phone[3:] if phone.startswith("+98") else phone
    if len(local) <= 8:
        return f"{local[:2]}***"
    return f"{local[:4]}***{local[-4:]}"


def confirmation_text(request):
    """The bot's confirmation message. Link requests read request.user (ORM)."""
    if request.purpose == TelegramAuthPurpose.LOGIN:
        return (
            "🔐 ورود به اپلیکیشن با تلگرام\n\n"
            "با تأیید، اپلیکیشنی که این لینک رو ساخته با همین حساب تلگرام وارد می‌شه "
            "و به حساب و سرویس‌هات دسترسی کامل پیدا می‌کنه.\n\n"
            "⚠️ فقط اگه همین الان خودت توی اپلیکیشن «ورود با تلگرام» رو زدی ادامه بده. "
            "اگه کسی این لینک رو برات فرستاده، «انصراف» رو بزن.\n\n"
            "برای تأیید، همون کدی رو بزن که الان توی اپلیکیشن می‌بینی:"
        )
    label = mask_account(request.user)
    if label:
        target = f"این حساب تلگرام به حساب اپلیکیشن «{label}» وصل می‌شه. "
    else:
        target = (
            "این حساب تلگرام به حساب اپلیکیشنی وصل می‌شه که این لینک رو ساخته "
            "(اون حساب شماره یا ایمیل تأییدشده‌ای نداره که اینجا نشونش بدیم). "
        )
    return (
        "🔗 اتصال تلگرام به حساب اپلیکیشن\n\n"
        f"{target}"
        "سرویس‌ها و فیش‌های پرداختی که توی ربات داری به همون حساب منتقل می‌شن "
        "و از این به بعد ربات و اپلیکیشن یک حساب هستن.\n\n"
        "⚠️ فقط اگه اون حساب مال خودته و همین الان خودت از داخل اپلیکیشن "
        "اتصال تلگرام رو درخواست کردی ادامه بده. اگه کسی این لینک رو برات فرستاده، "
        "«انصراف» رو بزن؛ وگرنه سرویس‌هات به حساب اون منتقل می‌شن.\n\n"
        "برای تأیید، همون کدی رو بزن که الان توی اپلیکیشن می‌بینی:"
    )


def code_choices(request):
    """
    The right code plus two distinct decoys, shuffled - the same three in
    the same order every time for a given request.

    Fixed per request, not drawn afresh: the opener may open the link as
    often as they like, and with new decoys each time the right code would
    be the only one that repeats - two or three opens and the one-in-three
    pick is gone. Derived rather than stored: an HMAC of the request id
    under SECRET_KEY seeds the draw, so nobody without the key can predict
    the decoys, and the row needs no extra column.
    """
    seed = salted_hmac(
        "apps.bot.telegram_auth.code_choices", str(request.pk), algorithm="sha256",
    ).digest()
    # random.Random is fine here: its seed is secret, and all it produces
    # is two numbers and an order.
    rng = random.Random(seed)
    choices = {request.code}
    while len(choices) < _CHOICE_COUNT:
        choices.add(f"{rng.randrange(10_000):04d}")
    # Sorted before the shuffle: a set's order changes between processes
    # (string hashing is randomised), which would change the buttons.
    choices = sorted(choices)
    rng.shuffle(choices)
    return choices


# ---------------------------------------------------------------------------
# Confirming (bot side, a code button)
# ---------------------------------------------------------------------------

def confirm_request(request_id, telegram_user, picked_code):
    """
    Acts on a pressed button and returns the request in its final state.

    A wrong code cancels the request outright: a second guess would turn
    one-in-three into certainty for someone clicking through a phishing
    link. Raises, writing nothing, when the presser isn't the opener
    (ForbiddenException), the request has expired (TelegramAuthExpired),
    or it is unknown or already finished (another AppException) - all with
    Persian messages for the bot.
    """
    try:
        request_id = uuid.UUID(str(request_id))
    except ValueError:
        raise NotFoundException(_NOT_FOUND_FA)

    with transaction.atomic():
        # Locked so a double tap, or the same buttons in two copies of the
        # message, settle the request exactly once.
        request = (
            TelegramAuthRequest.objects.select_for_update()
            .filter(pk=request_id)
            .first()
        )
        if request is None:
            raise NotFoundException(_NOT_FOUND_FA)
        if request.opened_by_telegram_id != telegram_user.id:
            raise ForbiddenException("این دکمه‌ها فقط برای کسیه که لینک رو باز کرده.")
        if request.status != TelegramAuthStatus.PENDING:
            raise BadRequestException(_closed_text_fa(request))
        if request.is_expired:
            raise TelegramAuthExpired(_closed_text_fa(request))

        request.telegram_user_id = telegram_user.id
        if picked_code == CANCEL_CHOICE:
            _fail(request, REASON_CANCELLED, status=TelegramAuthStatus.CANCELLED)
        elif not hmac.compare_digest(str(picked_code).encode(), request.code.encode()):
            _fail(request, REASON_WRONG_CODE, status=TelegramAuthStatus.CANCELLED)
        elif request.purpose == TelegramAuthPurpose.LOGIN:
            _confirm_login(request, telegram_user)
        else:
            _confirm_link(request, telegram_user)

    return request


def _fail(request, reason, *, status=TelegramAuthStatus.FAILED):
    request.status = status
    request.failure_reason = reason
    request.save(update_fields=["status", "failure_reason", "telegram_user_id"])


def _succeed(request, user, *, moved=0, merged_from=None):
    request.status = TelegramAuthStatus.CONFIRMED
    request.user = user
    request.moved_subscriptions = moved
    request.merged_from = merged_from
    request.confirmed_at = timezone.now()
    request.save(update_fields=[
        "status", "user", "moved_subscriptions", "merged_from", "confirmed_at",
        "telegram_user_id",
    ])


def _lock_profile(telegram_user_id):
    # Taken first by both login and link, so two confirmations for the same
    # Telegram queue up instead of each reading a profile the other is
    # about to change. It locks nothing when there is no profile yet; the
    # unique telegram_user_id catches that race instead (see _confirm_link).
    return (
        TelegramProfile.objects.select_for_update()
        .filter(telegram_user_id=telegram_user_id)
        .first()
    )


def _confirm_login(request, telegram_user):
    """Same rules as /start: an existing account, or a new one unless invites are required."""
    _lock_profile(telegram_user.id)
    profile = get_or_create_telegram_user(telegram_user)
    if profile is None:
        _fail(request, REASON_INVITE_REQUIRED)
        return
    if not profile.user.is_active:
        _fail(request, REASON_ACCOUNT_DISABLED)
        return
    _succeed(request, profile.user)


def _apply_names(profile, telegram_user):
    profile.telegram_username = telegram_user.username or ""
    profile.telegram_first_name = telegram_user.first_name or ""


def _confirm_link(request, telegram_user):
    profile = _lock_profile(telegram_user.id)

    # Users after the profile, in primary-key order - the same order every
    # link confirmation and merge_bot_account_into use, so none of them can
    # deadlock another. Locking the target also serialises two links racing
    # into the same app account.
    user_ids = {request.user_id}
    if profile is not None and profile.user_id:
        user_ids.add(profile.user_id)
    users = {
        user.pk: user
        for user in User.objects.select_for_update().filter(pk__in=user_ids).order_by("pk")
    }
    target = users[request.user_id]

    if not target.is_active:
        _fail(request, REASON_ACCOUNT_DISABLED)
        return

    # Re-checked here, not only when the link was started: another link
    # may have finished in the ten minutes since.
    existing = TelegramProfile.objects.filter(user=target).first()
    if existing is not None:
        if profile is not None and existing.pk == profile.pk:
            _succeed(request, target)  # already this Telegram - nothing to do
        else:
            _fail(request, REASON_ALREADY_CONNECTED)
        return

    moved = 0
    merged_from = None
    if profile is None:
        # Never used the bot. No invite code needed: the account already exists.
        try:
            with transaction.atomic():
                TelegramProfile.objects.create(
                    user=target,
                    telegram_user_id=telegram_user.id,
                    telegram_username=telegram_user.username or "",
                    telegram_first_name=telegram_user.first_name or "",
                )
        except IntegrityError:
            # /start created a profile for this Telegram since the lock
            # above found none.
            _fail(request, REASON_RETRY)
            return

    elif profile.user_id is None:
        # Ran /start and is still owing an invite code. Attached as is,
        # and no longer waiting for that code.
        profile.user = target
        profile.awaiting_action = ""
        _apply_names(profile, telegram_user)
        profile.save(update_fields=[
            "user", "awaiting_action", "telegram_username", "telegram_first_name", "updated_at",
        ])

    else:
        source = users[profile.user_id]
        if not is_bot_only_account(source):
            # A real account behind this Telegram: its owner signs in there.
            # Moving it would strand whoever owns its email or phone.
            _fail(request, REASON_TELEGRAM_TAKEN)
            return
        if not source.is_active:
            # Disabled by an admin; merging would bring it back to life
            # inside the app account.
            _fail(request, REASON_TELEGRAM_ACCOUNT_DISABLED)
            return
        try:
            moved = merge_bot_account_into(source, target)
        except AppException as exc:
            # Rolled back by merge's own savepoint. Unreachable after the
            # checks above unless something changed under the locks.
            logger.warning("Merge for Telegram link %s refused: %s", request.pk, exc.message)
            _fail(request, REASON_RETRY)
            return
        merged_from = source
        profile.refresh_from_db()
        _apply_names(profile, telegram_user)
        profile.save(update_fields=["telegram_username", "telegram_first_name", "updated_at"])

    _succeed(request, target, moved=moved, merged_from=merged_from)


# ---------------------------------------------------------------------------
# Polling (app side)
# ---------------------------------------------------------------------------

def _get_login_request(request_id, poll_secret):
    request = TelegramAuthRequest.objects.filter(
        pk=request_id, purpose=TelegramAuthPurpose.LOGIN,
    ).first()
    # One answer for both cases: saying which half was wrong would confirm
    # that a request id exists.
    if request is None or not hmac.compare_digest(
        request.poll_secret_hash, _hash_secret(poll_secret)
    ):
        raise NotFoundException("Sign-in request not found.")
    return request


def poll_login(request_id, poll_secret):
    """
    The login request's state for the app. The one call that finds it
    confirmed also collects the session and marks it used, so a session is
    issued exactly once; that response reports "confirmed" with the
    session, and later ones report "used" without.
    """
    request = _get_login_request(request_id, poll_secret)
    session = None

    if request.status == TelegramAuthStatus.CONFIRMED:
        with transaction.atomic():
            # Re-read under the lock: two polls in flight both saw
            # "confirmed" above, and only one may leave with a session.
            request = TelegramAuthRequest.objects.select_for_update().get(pk=request.pk)
            if request.status == TelegramAuthStatus.CONFIRMED:
                user = User.objects.get(pk=request.user_id)
                if user.is_active:
                    session = auth_payload(user)
                    request.status = TelegramAuthStatus.USED
                    request.save(update_fields=["status"])
                else:
                    # Disabled between the bot confirming and the app
                    # collecting - e.g. merged into another account.
                    _fail(request, REASON_ACCOUNT_DISABLED)

    status = TelegramAuthStatus.CONFIRMED.value if session else request.effective_status
    return {
        "status": status,
        "message": _app_message(request, status),
        "session": session,
    }


def link_status(request_id, user):
    """A link request's state for the account that started it. 404 for anyone else's."""
    request = TelegramAuthRequest.objects.filter(
        pk=request_id, purpose=TelegramAuthPurpose.LINK, user=user,
    ).first()
    if request is None:
        raise NotFoundException("Request not found.")

    status = request.effective_status
    return {
        "status": status,
        "message": _app_message(request, status),
        "moved_subscriptions": request.moved_subscriptions,
        # Fetched again: request.user is the one authenticated at the start
        # of this HTTP request and may predate the link.
        "user": UserInfoSerializer(User.objects.get(pk=user.pk)).data,
    }
