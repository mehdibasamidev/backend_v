from django.db import transaction
from django.db.models import F

from apps.referral.models import (
    Referral,
    ReferralCode,
    ReferralCodeKind,
    ReferralSettings,
)
from config.utils.exceptions import BadRequestException


def referral_required():
    return ReferralSettings.get_solo().required_for_signup


def normalize_code(raw):
    """
    Codes are shared by voice and screenshot, so they arrive with stray
    spaces, dashes and mixed case. One canonical form here means the lookup
    doesn't have to guess.
    """
    return (raw or "").strip().upper().replace(" ", "").replace("-", "")


def resolve_code(raw):
    """
    Returns the ReferralCode for `raw`, or raises with a reason the user can
    act on.

    The messages distinguish "no such code" from "this one is used up"
    because they call for different actions - retyping versus asking for a
    different code - and neither leaks anything: a referral code is meant
    to be shared.
    """
    code = normalize_code(raw)
    if not code:
        raise BadRequestException("Enter an invite code.")

    instance = ReferralCode.objects.filter(code=code).first()
    if instance is None:
        raise BadRequestException("That invite code doesn't exist.")
    if not instance.is_active:
        raise BadRequestException("That invite code has been disabled.")
    if instance.is_expired:
        raise BadRequestException("That invite code has expired.")
    if instance.is_exhausted:
        raise BadRequestException("That invite code has already been used up.")

    return instance


def validate_for_signup(raw):
    """
    Checks a code before an account exists. Used by the signup serializers
    so a bad code fails at the door rather than after the OTP round trip.
    """
    if not referral_required() and not (raw or "").strip():
        return None
    return resolve_code(raw)


@transaction.atomic
def redeem(code_input, user):
    """
    Records a redemption and consumes one use.

    The counter is bumped with an F() expression inside a locked read, not
    read-modify-write in Python: two people redeeming the last use of a
    code at the same moment would otherwise both see used_count = max - 1
    and both succeed.
    """
    code = normalize_code(code_input)
    instance = (
        ReferralCode.objects.select_for_update().filter(code=code).first()
    )
    if instance is None or not instance.is_usable:
        raise BadRequestException("That invite code is no longer valid.")

    # Re-checked under the lock: is_usable above was read before it, and the
    # gap is exactly where a race lands.
    if instance.max_uses > 0 and instance.used_count >= instance.max_uses:
        raise BadRequestException("That invite code has already been used up.")

    if instance.owner_id == user.id:
        raise BadRequestException("You can't use your own invite code.")

    referral = Referral.objects.create(
        code=instance,
        referrer=instance.owner,
        referred_user=user,
    )

    ReferralCode.objects.filter(pk=instance.pk).update(
        used_count=F("used_count") + 1
    )
    return referral


def get_or_create_personal_code(user):
    """
    The user's own invite code, minted on first request.

    Not created at signup: most people never share one, and generating a
    row for every account would fill the table with codes nobody has seen.
    """
    settings_row = ReferralSettings.get_solo()
    if not settings_row.personal_codes_enabled:
        raise BadRequestException("Invite codes are not available right now.")

    existing = ReferralCode.objects.filter(
        owner=user, kind=ReferralCodeKind.PERSONAL
    ).first()
    if existing:
        return existing

    # Collisions are vanishingly unlikely with a 31-character alphabet and
    # length 8, but a unique constraint failure at signup time is not worth
    # the risk of not retrying.
    for _ in range(10):
        candidate = ReferralCode.generate_code()
        if not ReferralCode.objects.filter(code=candidate).exists():
            return ReferralCode.objects.create(
                code=candidate,
                kind=ReferralCodeKind.PERSONAL,
                owner=user,
                max_uses=settings_row.default_personal_max_uses,
            )

    raise BadRequestException("Could not generate an invite code. Try again.")
