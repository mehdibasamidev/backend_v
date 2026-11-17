from django.contrib import admin

from apps.referral.models import CheckoutRewardUse, Referral, ReferralCode, ReferralReward, ReferralSettings


@admin.register(ReferralSettings)
class ReferralSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "required_for_signup",
        "personal_codes_enabled",
        "default_personal_max_uses",
        "reward_kind",
        "reward_value",
        "updated_at",
    )

    def has_add_permission(self, request):
        # Single row - get_solo() creates it; a second would make "which
        # setting is live" ambiguous.
        return not ReferralSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = (
        "code", "kind", "owner", "used_count", "max_uses",
        "is_active", "expires_at", "created_at",
    )
    list_filter = ("kind", "is_active")
    search_fields = ("code", "note", "owner__username", "owner__email")
    readonly_fields = ("id", "used_count", "created_at")
    raw_id_fields = ("owner",)

    def has_delete_permission(self, request, obj=None):
        # Redemptions PROTECT the code, so a delete would fail anyway once
        # anyone has used it. Disabling via is_active is the intended path.
        return False


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    """Reward status follows the payment and its ledger entry."""
    list_display = (
        "referred_user", "referrer", "code",
        "is_rewarded", "rewarded_at", "created_at",
    )
    list_filter = ("is_rewarded",)
    search_fields = (
        "code__code",
        "referred_user__username",
        "referred_user__email",
        "referrer__username",
    )
    readonly_fields = ("id", "code", "referrer", "referred_user", "created_at", "is_rewarded", "rewarded_at", "reward_note")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class RewardAuditAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReferralReward)
class ReferralRewardAdmin(RewardAuditAdmin):
    list_display = ("user", "kind", "value", "purchase", "created_at")
    list_select_related = ("user", "purchase")
    list_filter = ("kind",)


@admin.register(CheckoutRewardUse)
class CheckoutRewardUseAdmin(RewardAuditAdmin):
    list_display = ("user", "proof", "wallet_amount", "discount_amount", "released_at")
    list_select_related = ("user", "proof")
