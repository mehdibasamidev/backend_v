from django.contrib import admin

from apps.referral.models import Referral, ReferralCode, ReferralSettings


@admin.register(ReferralSettings)
class ReferralSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "required_for_signup",
        "personal_codes_enabled",
        "default_personal_max_uses",
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
    """
    The record the reward system will pay against. Read-only apart from the
    reward fields, which are the only thing an admin should be adjusting by
    hand.
    """
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
    readonly_fields = ("id", "code", "referrer", "referred_user", "created_at")
    raw_id_fields = ("code", "referrer", "referred_user")

    def has_add_permission(self, request):
        return False
