from django.contrib import admin, messages
from django.db.models import Count

from apps.vpn.models import (
    InboundGroup,
    VpnPlan,
    VpnPricingConfig,
    UserVpnSubscription,
    PaymentProof,
    XuiInbound,
)
from apps.vpn.services.inbounds import make_default_group
from apps.vpn.services.review import approve_payment_proof, reject_payment_proof
from config.utils.exceptions import AppException


@admin.register(XuiInbound)
class XuiInboundAdmin(admin.ModelAdmin):
    """Filled by `manage.py sync_xui_inbounds` or the app's Inbounds tab."""
    list_display = (
        "panel_id", "remark", "protocol", "port",
        "is_enabled_on_panel", "exists_on_panel", "last_synced_at",
    )
    list_filter = ("exists_on_panel", "is_enabled_on_panel", "protocol")
    search_fields = ("remark",)
    readonly_fields = (
        "remark", "protocol", "port", "is_enabled_on_panel",
        "exists_on_panel", "last_synced_at", "created_at", "updated_at",
    )

    def get_readonly_fields(self, request, obj=None):
        # An id can be typed in by hand before the first sync, but not
        # changed afterwards: every group holding the row would silently
        # start pointing at a different server.
        if obj is not None:
            return ("panel_id",) + self.readonly_fields
        return self.readonly_fields


@admin.register(InboundGroup)
class InboundGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "is_default", "inbound_count", "updated_at")
    search_fields = ("name",)
    filter_horizontal = ("inbounds",)
    # Switched only through the action below: the form would either trip
    # the one-default constraint or leave no default at all.
    readonly_fields = ("is_default", "created_at", "updated_at")
    actions = ["make_default"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(inbound_total=Count("inbounds"))

    def inbound_count(self, obj):
        return obj.inbound_total
    inbound_count.short_description = "Inbounds"
    inbound_count.admin_order_field = "inbound_total"

    def get_actions(self, request):
        # Bulk delete checks the model permission only, not
        # has_delete_permission(obj), so it could remove the default group.
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_default:
            return False
        return super().has_delete_permission(request, obj)

    def make_default(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one group.", level=messages.ERROR)
            return
        group = make_default_group(queryset.first())
        self.message_user(request, f"'{group.name}' is now the default group.")
    make_default.short_description = "Make the selected group the default"


@admin.register(VpnPlan)
class VpnPlanAdmin(admin.ModelAdmin):
    list_display = (
        "name", "volume_gb", "duration_days", "max_concurrent_users", "price",
        "inbound_group", "is_active", "order",
    )
    list_editable = ("is_active", "order")
    list_filter = ("is_active", "inbound_group")
    list_select_related = ("inbound_group",)
    search_fields = ("name",)


@admin.register(VpnPricingConfig)
class VpnPricingConfigAdmin(admin.ModelAdmin):
    list_display = ("price_per_gb", "price_per_extra_days", "price_per_extra_user", "is_active", "updated_at")
    list_filter = ("is_active",)


class PaymentProofInline(admin.StackedInline):
    model = PaymentProof
    extra = 0
    readonly_fields = ("created_at", "source", "ai_checked", "ai_verdict", "ai_notes", "admin_notified_at")
    ordering = ("-created_at",)


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
    list_display = (
        "subscription", "kind", "source", "amount", "is_approved", "ai_verdict",
        "reviewed_by", "reviewed_at", "admin_notified_at", "created_at",
    )
    list_filter = ("is_approved", "kind", "source", "ai_verdict")
    readonly_fields = ("source", "ai_checked", "ai_verdict", "ai_notes", "admin_notified_at", "created_at")
    actions = ["approve_selected", "reject_selected"]

    def approve_selected(self, request, queryset):
        count = 0
        for proof in queryset.filter(is_approved__isnull=True):
            try:
                approve_payment_proof(proof, reviewed_by=request.user)
            except AppException as exc:
                # e.g. the plan's inbound group is empty. The proof stays
                # pending, so it can be approved again once that is fixed.
                self.message_user(request, f"{proof}: {exc.message}", level=messages.ERROR)
                continue
            count += 1
        self.message_user(request, f"{count} payment(s) approved and applied on the VPN panel.")
    approve_selected.short_description = "Approve selected payments & apply on panel"

    def reject_selected(self, request, queryset):
        count = 0
        for proof in queryset.filter(is_approved__isnull=True):
            reject_payment_proof(proof, reviewed_by=request.user)
            count += 1
        self.message_user(request, f"{count} payment(s) rejected.")
    reject_selected.short_description = "Reject selected payments"
