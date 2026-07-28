from django.contrib import admin
from django.utils import timezone

from apps.vpn.models import (
    VpnPlan,
    VpnPricingConfig,
    UserVpnSubscription,
    PaymentProof,
)
from apps.vpn.services.provisioning import activate_subscription, reject_subscription


@admin.register(VpnPlan)
class VpnPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "volume_gb", "duration_days", "max_concurrent_users", "price", "is_active", "order")
    list_editable = ("is_active", "order")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(VpnPricingConfig)
class VpnPricingConfigAdmin(admin.ModelAdmin):
    list_display = ("price_per_gb", "price_per_extra_days", "price_per_extra_user", "is_active", "updated_at")
    list_filter = ("is_active",)


class PaymentProofInline(admin.StackedInline):
    model = PaymentProof
    extra = 0
    readonly_fields = ("created_at", "ai_checked", "ai_verdict", "ai_notes")


@admin.register(UserVpnSubscription)
class UserVpnSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "user", "label", "source", "status",
        "volume_gb", "duration_days", "max_concurrent_users",
        "remaining_days_display", "remaining_volume_display", "created_at",
    )
    list_filter = ("status", "source")
    search_fields = ("user__email", "label", "xui_client_email")
    readonly_fields = (
        "xui_client_uuid", "xui_client_subid", "xui_client_email", "xui_inbound_ids",
        "subscription_link", "used_traffic_bytes", "last_synced_at",
        "started_at", "expires_at",
    )
    inlines = [PaymentProofInline]

    def remaining_days_display(self, obj):
        return obj.remaining_days
    remaining_days_display.short_description = "Remaining days"

    def remaining_volume_display(self, obj):
        if obj.is_unlimited_volume:
            return "Unlimited"
        return f"{obj.remaining_volume_gb} GB"
    remaining_volume_display.short_description = "Remaining volume"


@admin.register(PaymentProof)
class PaymentProofAdmin(admin.ModelAdmin):
    list_display = ("subscription", "is_approved", "ai_verdict", "reviewed_by", "reviewed_at", "created_at")
    list_filter = ("is_approved", "ai_verdict")
    readonly_fields = ("ai_checked", "ai_verdict", "ai_notes", "created_at")
    actions = ["approve_selected", "reject_selected"]

    def approve_selected(self, request, queryset):
        count = 0
        for proof in queryset.filter(is_approved__isnull=True):
            proof.is_approved = True
            proof.reviewed_by = request.user
            proof.reviewed_at = timezone.now()
            proof.save(update_fields=["is_approved", "reviewed_by", "reviewed_at"])
            activate_subscription(proof.subscription)
            count += 1
        self.message_user(request, f"{count} payment(s) approved and activated on the VPN panel.")
    approve_selected.short_description = "Approve selected payments & activate VPN"

    def reject_selected(self, request, queryset):
        count = 0
        for proof in queryset.filter(is_approved__isnull=True):
            proof.is_approved = False
            proof.reviewed_by = request.user
            proof.reviewed_at = timezone.now()
            proof.save(update_fields=["is_approved", "reviewed_by", "reviewed_at"])
            reject_subscription(proof.subscription)
            count += 1
        self.message_user(request, f"{count} payment(s) rejected.")
    reject_selected.short_description = "Reject selected payments"
