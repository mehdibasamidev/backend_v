import uuid
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Avg
from django.utils import timezone
from django.utils.crypto import get_random_string


def custom_user_img_upload_to(instance, filename):
    return f"users/{instance.id}/profile_images/{filename}"


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Internal identity ONLY - never shown, never typed by anyone.
    #
    # Django requires USERNAME_FIELD to always be populated, but all three
    # real identifiers are optional here: a mobile-only signup has no email,
    # an email signup has no mobile, and username stays null until the user
    # picks one during onboarding. So none of them can carry that role.
    # Authentication happens in the custom backend against the real fields.
    identifier = models.CharField(max_length=64, unique=True, editable=False)

    # All three are unique-but-nullable. Postgres allows many NULLs in a
    # unique column, which is exactly what "optional identifier" needs -
    # blank strings would collide on the second row.
    email = models.EmailField(unique=True, null=True, blank=True, max_length=255)
    phone_number = models.CharField(unique=True, null=True, blank=True, max_length=20)
    username = models.CharField(max_length=150, unique=True, null=True, blank=True)

    # Ownership is only proven by passing an OTP. Both matter because both
    # are sign-in routes: an unverified phone number would let anyone claim
    # someone else's number and then sign in as them.
    is_email_verified = models.BooleanField(default=False)
    is_phone_verified = models.BooleanField(default=False)

    last_seen = models.DateTimeField(null=True, blank=True)
    full_name = models.CharField(max_length=255, blank=True)
    profile_picture = models.FileField(upload_to=custom_user_img_upload_to, blank=True)
    biography = models.TextField(blank=True)
    google_id = models.CharField(max_length=255, null=True, blank=True, unique=True)

    USERNAME_FIELD = "identifier"
    REQUIRED_FIELDS = []

    def save(self, *args, **kwargs):
        if not self.identifier:
            self.identifier = uuid.uuid4().hex
        # Normalise "" to None so the unique constraints keep working -
        # forms and serializers happily hand back empty strings.
        for field in ("email", "phone_number", "username"):
            if getattr(self, field) == "":
                setattr(self, field, None)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.username or self.email or self.phone_number or str(self.id)

    @property
    def has_usable_login_password(self):
        return self.has_usable_password()

    @property
    def needs_email_verification(self):
        """
        Drives the profile warning. Only meaningful when the admin has
        turned email verification on - and only for accounts that actually
        have an email to verify.
        """
        if not self.email or self.is_email_verified:
            return False
        return AuthSettings.get_solo().email_otp_required

    @property
    def rate(self):
        data = getattr(self, "received_feedback", None)
        if data:
            avg = data.aggregate(rate=Avg("rating"))["rate"]
            return round(avg, 2) if avg else 0.0
        return 0.0


class AuthSettings(models.Model):
    """
    Admin-controlled auth policy. Single row - use get_solo().

    Phone OTP is deliberately NOT switchable: it is the only proof that a
    number belongs to the person signing up, so turning it off would let
    anyone register with someone else's number and then sign in as them.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    email_otp_required = models.BooleanField(
        default=False,
        help_text=(
            "When on, a new email signup must confirm a code before the "
            "account is created, and existing unverified accounts are warned "
            "in their profile."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Auth Settings"
        verbose_name_plural = "Auth Settings"

    def __str__(self):
        return f"Auth settings (email OTP: {'on' if self.email_otp_required else 'off'})"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(defaults={})
        return obj


class OtpPurpose(models.TextChoices):
    REGISTER_PHONE = "register_phone", "Register with phone"
    REGISTER_EMAIL = "register_email", "Register with email"
    LOGIN_PHONE = "login_phone", "Sign in with phone"
    ADD_EMAIL = "add_email", "Attach an email"
    ADD_PHONE = "add_phone", "Attach a phone number"
    RESET_PASSWORD = "reset_password", "Reset password"


class OtpChannel(models.TextChoices):
    SMS = "sms", "SMS"
    EMAIL = "email", "Email"


class OtpCode(models.Model):
    """
    A one-time code sent to an email address or phone number.

    The code is stored HASHED. This table is effectively a set of live keys
    to user accounts, so a database leak with plaintext codes would hand
    over every account with a code in flight.

    `purpose` is enforced on verification: a code issued to sign in must not
    be usable to attach an email to someone else's account.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Email address or E.164 phone number the code was sent to.
    target = models.CharField(max_length=255, db_index=True)
    channel = models.CharField(max_length=10, choices=OtpChannel.choices)
    purpose = models.CharField(max_length=20, choices=OtpPurpose.choices)

    code_hash = models.CharField(max_length=128)

    # Null for registration - the account does not exist yet.
    user = models.ForeignKey(
        "account.User",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="otp_codes",
    )

    # Carries whatever the pending action needs, e.g. the hashed password
    # for an email signup. Kept here rather than in a half-built User row so
    # an abandoned signup never squats on an email address.
    payload = models.JSONField(default=dict, blank=True)

    attempts = models.PositiveSmallIntegerField(default=0)
    is_used = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    MAX_ATTEMPTS = 5
    TTL = timedelta(minutes=2)
    RESEND_COOLDOWN = timedelta(seconds=60)
    MAX_PER_TARGET_PER_DAY = 10

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["target", "purpose", "-created_at"])]

    def __str__(self):
        return f"{self.purpose} -> {self.target}"

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def is_spent(self):
        return self.is_used or self.is_expired or self.attempts >= self.MAX_ATTEMPTS

    def check_code(self, raw_code):
        return check_password(raw_code, self.code_hash)

    @staticmethod
    def generate_raw_code(length=6):
        # Digits only: the code is read off an SMS and typed into a numeric
        # keypad, and Kavenegar templates reject tokens with spaces.
        return get_random_string(length, allowed_chars="0123456789")

    @classmethod
    def issue(cls, target, channel, purpose, user=None, payload=None, forced_code=None):
        raw = forced_code or cls.generate_raw_code()
        instance = cls.objects.create(
            target=target,
            channel=channel,
            purpose=purpose,
            user=user,
            payload=payload or {},
            code_hash=make_password(raw),
            expires_at=timezone.now() + cls.TTL,
        )
        return instance, raw
