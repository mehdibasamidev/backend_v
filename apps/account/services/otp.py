import logging
from datetime import timedelta

from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone

from apps.account.models import OtpChannel, OtpCode
from apps.account.services.sms import send_otp_sms
from config.utils.exceptions import BadRequestException

logger = logging.getLogger("apps")


def _enforce_send_limits(target, purpose):
    """
    Every send costs money and lands on someone's phone, so an unthrottled
    endpoint is both a bill and a way to harass a stranger. Two limits:
    a short cooldown that stops rapid re-taps, and a daily ceiling that
    caps what a script can do overnight.
    """
    now = timezone.now()

    latest = OtpCode.objects.filter(target=target, purpose=purpose).first()
    if latest and now - latest.created_at < OtpCode.RESEND_COOLDOWN:
        wait = int((OtpCode.RESEND_COOLDOWN - (now - latest.created_at)).total_seconds())
        raise BadRequestException(
            f"Please wait {wait} seconds before requesting another code."
        )

    sent_today = OtpCode.objects.filter(
        target=target, created_at__gte=now - timedelta(days=1)
    ).count()
    if sent_today >= OtpCode.MAX_PER_TARGET_PER_DAY:
        raise BadRequestException(
            "Too many codes requested for this number today. Try again tomorrow."
        )


def send_otp(target, channel, purpose, user=None, payload=None):
    """
    Issues a code and delivers it. Returns the OtpCode row.

    Any previously live code for the same target+purpose is invalidated
    first, so an older SMS still sitting in someone's inbox cannot be
    replayed after they asked for a fresh one.
    """
    _enforce_send_limits(target, purpose)

    OtpCode.objects.filter(
        target=target, purpose=purpose, is_used=False
    ).update(is_used=True)

    otp, raw_code = OtpCode.issue(
        target=target, channel=channel, purpose=purpose, user=user, payload=payload
    )

    try:
        if channel == OtpChannel.SMS:
            send_otp_sms(target, raw_code)
        else:
            send_mail(
                subject="کد تایید",
                message=f"کد تایید شما: {raw_code}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[target],
                fail_silently=False,
            )
    except Exception:
        # Delivery failed, so the row must not linger - otherwise the
        # cooldown blocks the retry the user is about to make for a code
        # they never received.
        otp.delete()
        raise

    return otp


def verify_otp(target, code, purpose):
    """
    Checks a code and consumes it. Returns the OtpCode row so the caller
    can read `payload` and `user`.

    Attempts are counted per code and the code dies at the limit, so a
    six-digit space cannot be walked through by brute force. The error text
    stays vague on purpose - saying "wrong code" versus "expired" tells an
    attacker which half of the guess was right.
    """
    otp = (
        OtpCode.objects.filter(target=target, purpose=purpose, is_used=False)
        .order_by("-created_at")
        .first()
    )

    if otp is None or otp.is_expired:
        raise BadRequestException("This code is invalid or has expired. Request a new one.")

    if otp.attempts >= OtpCode.MAX_ATTEMPTS:
        raise BadRequestException("Too many incorrect attempts. Request a new code.")

    if not otp.check_code(str(code).strip()):
        otp.attempts += 1
        otp.save(update_fields=["attempts"])
        remaining = OtpCode.MAX_ATTEMPTS - otp.attempts
        if remaining <= 0:
            raise BadRequestException("Too many incorrect attempts. Request a new code.")
        raise BadRequestException(f"Incorrect code. {remaining} attempt(s) left.")

    otp.is_used = True
    otp.save(update_fields=["is_used"])
    return otp
