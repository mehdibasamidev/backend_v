import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import get_random_string


class RewardKind(models.TextChoices):
    DISABLED = "disabled", "Disabled"
    WALLET = "wallet", "Fixed wallet credit (toman)"
    DISCOUNT = "discount", "One-time discount (%)"


def validate_reward_policy(kind, value):
    if kind != RewardKind.DISABLED and value <= 0:
        raise ValidationError({"reward_value": "Enter a positive reward value."})
    if kind == RewardKind.DISCOUNT and value > 100:
        raise ValidationError({"reward_value": "The discount cannot exceed 100%."})


class ReferralSettings(models.Model):
    """
    Admin-controlled referral policy. Single row - use get_solo().
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    required_for_signup = models.BooleanField(
        default=False,
        help_text=(
            "When on, a new account cannot be created without a valid code. "
            "Existing accounts are never affected."
        ),
    )

    # Personal codes are minted on demand, so this only decides whether the
    # app offers one - turning it off doesn't invalidate codes already out
    # there, which would strand people mid-signup.
    personal_codes_enabled = models.BooleanField(
        default=True,
        help_text="Let every user share their own invite code.",
    )

    default_personal_max_uses = models.PositiveIntegerField(
        default=0,
        help_text="How many signups one user's personal code allows. 0 = unlimited.",
    )
    reward_kind = models.CharField(
        max_length=10, choices=RewardKind.choices, default=RewardKind.DISABLED,
    )
    reward_value = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0"),
        help_text="Toman for wallet credit or percentage for a discount. Ignored when disabled.",
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Referral Settings"
        verbose_name_plural = "Referral Settings"

    def __str__(self):
        state = "required" if self.required_for_signup else "optional"
        return f"Referral settings ({state})"

    def clean(self):
        super().clean()
        if self.reward_value is not None:
            validate_reward_policy(self.reward_kind, self.reward_value)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(defaults={})
        return obj


class ReferralCodeKind(models.TextChoices):
    PERSONAL = "personal", "Personal invite"
    ADMIN = "admin", "Admin-issued"


class ReferralCode(models.Model):
    """
    An invite code.

    Both kinds live in one table on purpose: a personal code and an
    admin-issued one are redeemed identically, and splitting them would mean
    every lookup checking two places and every uniqueness guarantee spanning
    both.

    `owner` is what tells them apart - null means admin-issued, with nobody
    to credit.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    code = models.CharField(max_length=32, unique=True, db_index=True)
    kind = models.CharField(max_length=10, choices=ReferralCodeKind.choices)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="referral_codes",
        help_text="Null for admin-issued codes.",
    )

    # 0 means unlimited, matching the convention used for plan volume and
    # concurrent users elsewhere in this project.
    max_uses = models.PositiveIntegerField(default=0)
    used_count = models.PositiveIntegerField(default=0)

    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    note = models.CharField(
        max_length=200,
        blank=True,
        help_text="Admin-only label, e.g. which campaign this belongs to.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["code", "is_active"])]

    def __str__(self):
        return f"{self.code} ({self.kind})"

    @property
    def is_exhausted(self):
        return self.max_uses > 0 and self.used_count >= self.max_uses

    @property
    def is_expired(self):
        return self.expires_at is not None and timezone.now() > self.expires_at

    @property
    def is_usable(self):
        return self.is_active and not self.is_exhausted and not self.is_expired

    @property
    def remaining_uses(self):
        if self.max_uses == 0:
            return None
        return max(self.max_uses - self.used_count, 0)

    @staticmethod
    def generate_code(length=8):
        # No 0/O/1/I/L: these codes get read aloud and retyped from
        # screenshots, and those four are where that goes wrong.
        alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
        return get_random_string(length, allowed_chars=alphabet)


class Referral(models.Model):
    """
    One redemption: who invited whom, and with which code.

    Recorded separately from the code's counter so the reward system that
    comes later has something to pay against - a bare `used_count` would
    say a code was used five times but not by whom, and could never be
    reconciled.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    code = models.ForeignKey(
        ReferralCode,
        on_delete=models.PROTECT,
        related_name="redemptions",
    )

    # Denormalised from code.owner at redemption time. The code could later
    # be deleted or reassigned, and a reward must still know who earned it.
    referrer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referrals_made",
    )

    referred_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="referral_source",
    )

    # Kept for the existing referral API and historic reward records.
    is_rewarded = models.BooleanField(default=False)
    rewarded_at = models.DateTimeField(null=True, blank=True)
    reward_note = models.CharField(max_length=200, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.referred_user_id} via {self.code.code}"


class ReferralReward(models.Model):
    """An earned credit or coupon, with the policy frozen at approval."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    referral = models.OneToOneField(Referral, on_delete=models.PROTECT, related_name="reward")
    purchase = models.OneToOneField("vpn.PaymentProof", on_delete=models.PROTECT, related_name="referral_reward")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="earned_referral_rewards")
    kind = models.CharField(max_length=10, choices=RewardKind.choices)
    value = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                check=Q(kind=RewardKind.WALLET, value__gt=0)
                | Q(kind=RewardKind.DISCOUNT, value__gt=0, value__lte=100),
                name="referral_reward_valid_value",
            ),
        ]


class CheckoutRewardUse(models.Model):
    """A reservation that becomes a spend on approval. Rejection releases it."""

    proof = models.OneToOneField("vpn.PaymentProof", on_delete=models.PROTECT, related_name="reward_use")
    request_id = models.UUIDField(unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="checkout_reward_uses")
    wallet_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    discount = models.ForeignKey(ReferralReward, on_delete=models.PROTECT, null=True, blank=True, related_name="uses")
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(wallet_amount__gt=0, discount__isnull=True, discount_amount=0)
                | Q(wallet_amount=0, discount__isnull=False, discount_amount__gt=0),
                name="referral_checkout_one_reward",
            ),
            models.UniqueConstraint(
                fields=["discount"], condition=Q(discount__isnull=False, released_at__isnull=True),
                name="referral_checkout_unique_discount",
            ),
        ]
