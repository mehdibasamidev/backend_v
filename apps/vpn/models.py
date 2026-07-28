import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class PlanSourceChoices(models.TextChoices):
    FIXED = "fixed", "Fixed Plan"
    CUSTOM = "custom", "Custom Plan"


class SubscriptionStatusChoices(models.TextChoices):
    PENDING_PAYMENT = "pending_payment", "Pending Payment"
    PENDING_APPROVAL = "pending_approval", "Pending Admin Approval"
    ACTIVE = "active", "Active"
    EXPIRED = "expired", "Expired"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class VpnPlan(models.Model):
    """
    Admin-defined fixed plan, e.g. "30GB / 30 Days / 2 concurrent users".
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    volume_gb = models.PositiveIntegerField(help_text="Total traffic volume in GB")
    duration_days = models.PositiveIntegerField(help_text="Total validity in days")
    max_concurrent_users = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0, help_text="Display order")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "price"]

    def __str__(self):
        return f"{self.name} ({self.volume_gb}GB/{self.duration_days}d)"

    @property
    def is_unlimited_volume(self):
        # Convention shared with the 3x-ui panel: volume_gb == 0 means "no cap".
        return self.volume_gb == 0

    @property
    def is_unlimited_users(self):
        # Convention shared with the 3x-ui panel: max_concurrent_users == 0 means "no cap".
        return self.max_concurrent_users == 0


class VpnPricingConfig(models.Model):
    """
    Server-side pricing rules used to price CUSTOM (user-built) plans.
    Only one row should have is_active=True at a time; use get_active().
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    price_per_gb = models.DecimalField(max_digits=10, decimal_places=2)
    price_per_extra_days = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Charged only for days beyond free_days",
    )
    price_per_extra_user = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Price for each concurrent user beyond the first",
    )
    base_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    free_days = models.PositiveIntegerField(
        default=30,
        help_text="First N days of a custom plan's duration are free; only days beyond this are billed",
    )

    min_gb = models.PositiveIntegerField(default=10)
    max_gb = models.PositiveIntegerField(default=1000)
    gb_step = models.PositiveIntegerField(default=10)

    min_days = models.PositiveIntegerField(default=7)
    max_days = models.PositiveIntegerField(default=365)

    min_users = models.PositiveIntegerField(default=1)
    max_users = models.PositiveIntegerField(default=5)

    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "VPN Pricing Config"
        verbose_name_plural = "VPN Pricing Config"

    def __str__(self):
        return f"Pricing config (active={self.is_active})"

    @classmethod
    def get_active(cls):
        config = cls.objects.filter(is_active=True).first()
        if not config:
            raise ValueError("No active VpnPricingConfig found. Please configure pricing in admin.")
        return config


class UserVpnSubscription(models.Model):
    """
    A single purchased/activated VPN service instance owned by a user.
    A user can own many of these at once (one for himself, one for a friend, etc.).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="vpn_subscriptions",
    )
    label = models.CharField(
        max_length=100, blank=True,
        help_text="Optional user-given name, e.g. 'For my brother'",
    )

    source = models.CharField(max_length=10, choices=PlanSourceChoices.choices)
    plan = models.ForeignKey(
        VpnPlan, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="subscriptions",
    )

    # Snapshot values copied at purchase time, so later plan/price edits
    # never affect subscriptions that were already purchased.
    volume_gb = models.PositiveIntegerField()
    duration_days = models.PositiveIntegerField()
    max_concurrent_users = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=12, decimal_places=2)

    status = models.CharField(
        max_length=20,
        choices=SubscriptionStatusChoices.choices,
        default=SubscriptionStatusChoices.PENDING_PAYMENT,
    )

    # Usage bookkeeping (synced from the 3x-ui panel)
    used_traffic_bytes = models.BigIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    # 3x-ui panel linkage
    xui_inbound_ids = models.JSONField(default=list, blank=True)
    xui_client_uuid = models.CharField(max_length=64, null=True, blank=True)
    xui_client_subid = models.CharField(max_length=64, null=True, blank=True)
    xui_client_email = models.CharField(max_length=150, null=True, blank=True, unique=True)
    subscription_link = models.URLField(max_length=500, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.email} - {self.volume_gb}GB/{self.duration_days}d ({self.status})"

    @property
    def remaining_days(self):
        if not self.expires_at:
            return self.duration_days
        remaining = (self.expires_at - timezone.now()).days
        return max(remaining, 0)

    @property
    def is_unlimited_volume(self):
        # Convention shared with the 3x-ui panel: volume_gb == 0 means "no cap".
        return self.volume_gb == 0

    @property
    def is_unlimited_users(self):
        # Convention shared with the 3x-ui panel: max_concurrent_users == 0 means "no cap".
        return self.max_concurrent_users == 0

    @property
    def remaining_volume_gb(self):
        if self.is_unlimited_volume:
            return None
        total_bytes = self.volume_gb * (1024 ** 3)
        remaining = total_bytes - self.used_traffic_bytes
        return max(round(remaining / (1024 ** 3), 2), 0)

    @property
    def is_expired(self):
        if self.status == SubscriptionStatusChoices.EXPIRED:
            return True
        if self.expires_at and timezone.now() > self.expires_at:
            return True
        return False


def payment_proof_upload_to(instance, filename):
    """
    مسیر ذخیره فیش پرداخت کاربر:
    users/<user_id>/proofs/<subscription_id>/<short_uuid>.<ext>
    """
    # ext = os.path.splitext(filename)[1].lower()
    # short_uuid = uuid.uuid4().hex[:10]
    # استخراج user_id از طریق رابطه‌ی subscription
    user_id = instance.subscription.user_id
    sub_id = instance.subscription_id

    return f"users/{user_id}/proofs/{sub_id}/{filename}"


class PaymentProof(models.Model):
    """
    Manual card-to-card payment proof submitted by the user for a subscription.
    Approval is always a human (admin) decision - the AI fields below are
    only an assistive opinion, never an auto-approval mechanism.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.OneToOneField(
        UserVpnSubscription, on_delete=models.CASCADE, related_name="payment_proof",
    )
    receipt_image = models.FileField(upload_to=payment_proof_upload_to, max_length=500, blank=True)
    receipt_text = models.TextField(blank=True, help_text="Transaction reference / free text note from the user")

    ai_checked = models.BooleanField(default=False)
    ai_verdict = models.CharField(max_length=20, blank=True, help_text="e.g. 'likely_genuine', 'suspicious'")
    ai_notes = models.TextField(blank=True)

    is_approved = models.BooleanField(null=True, help_text="null = pending, True = approved, False = rejected")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reviewed_payment_proofs",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Proof for {self.subscription_id} (approved={self.is_approved})"
