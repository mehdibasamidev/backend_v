from rest_framework import serializers

from apps.vpn.models import (
    InboundGroup,
    VpnPlan,
    VpnPricingConfig,
    UserVpnSubscription,
    PaymentProof,
    XuiInbound,
)


class AdminVpnPlanSerializer(serializers.ModelSerializer):
    """Full read/write access - unlike the public serializer this exposes
    is_active and order so admins can manage the catalogue."""

    # null = the default group, resolved at approval time.
    inbound_group = serializers.PrimaryKeyRelatedField(
        queryset=InboundGroup.objects.all(), allow_null=True, required=False,
    )
    # allow_null, not default=None: DRF skips a field's default on partial
    # updates, which dropped this key from PATCH responses for plans on
    # the default group.
    inbound_group_name = serializers.CharField(
        source="inbound_group.name", read_only=True, allow_null=True,
    )

    class Meta:
        model = VpnPlan
        fields = [
            "id", "name", "description",
            "volume_gb", "duration_days", "max_concurrent_users",
            "price", "is_active", "is_featured", "order",
            "inbound_group", "inbound_group_name",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_name(self, value):
        # seed_vpn_plans matches on name, so duplicates would make it
        # ambiguous which row a re-seed updates.
        qs = VpnPlan.objects.filter(name=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A plan with this name already exists.")
        return value


class AdminPricingConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = VpnPricingConfig
        fields = [
            "id",
            "price_per_gb", "price_per_extra_days", "price_per_extra_user",
            "base_price", "free_days",
            "min_gb", "max_gb", "gb_step",
            "min_days", "max_days",
            "min_users", "max_users",
            "is_active", "updated_at",
        ]
        read_only_fields = ["id", "updated_at"]

    def validate(self, attrs):
        def resolve(field):
            if field in attrs:
                return attrs[field]
            return getattr(self.instance, field, None)

        pairs = [("min_gb", "max_gb"), ("min_days", "max_days"), ("min_users", "max_users")]
        for lo_field, hi_field in pairs:
            lo, hi = resolve(lo_field), resolve(hi_field)
            if lo is not None and hi is not None and lo > hi:
                raise serializers.ValidationError(
                    {hi_field: f"{hi_field} must be greater than or equal to {lo_field}."}
                )

        step = resolve("gb_step")
        if step is not None and step <= 0:
            raise serializers.ValidationError({"gb_step": "gb_step must be greater than zero."})

        return attrs


class AdminXuiInboundSerializer(serializers.ModelSerializer):
    class Meta:
        model = XuiInbound
        fields = [
            "id", "panel_id", "remark", "protocol", "port",
            "is_enabled_on_panel", "exists_on_panel", "last_synced_at",
        ]
        read_only_fields = fields


class AdminInboundGroupSerializer(serializers.ModelSerializer):
    """
    Groups are written with PANEL inbound ids (what the admin sees on 3x-ui),
    not our row uuids. Any mirrored inbound is accepted, including one the
    last sync flagged missing, so re-saving a group that lost a server
    doesn't fail - provisioning skips missing ones anyway.

    Expects the queryset from services.inbounds.inbound_groups_with_details()
    so the list endpoint stays at a fixed number of queries.
    """
    name = serializers.CharField(max_length=100)
    is_default = serializers.BooleanField(required=False)
    inbound_ids = serializers.SlugRelatedField(
        source="inbounds",
        slug_field="panel_id",
        many=True,
        allow_empty=False,
        queryset=XuiInbound.objects.all(),
    )
    inbounds = AdminXuiInboundSerializer(many=True, read_only=True)
    plan_count = serializers.SerializerMethodField()

    class Meta:
        model = InboundGroup
        fields = [
            "id", "name", "is_default", "inbound_ids", "inbounds",
            "plan_count", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_plan_count(self, obj):
        count = getattr(obj, "plan_count", None)
        return obj.plans.count() if count is None else count

    def validate_name(self, value):
        qs = InboundGroup.objects.filter(name__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A group with this name already exists.")
        return value

    def validate_is_default(self, value):
        # There must always be a default - custom plans have no group of
        # their own - so it can only be moved, never switched off.
        if value is False and self.instance is not None and self.instance.is_default:
            raise serializers.ValidationError("Make another group the default instead.")
        return value


class AdminUserBriefSerializer(serializers.Serializer):
    """Just enough to identify the buyer in an admin list."""
    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)
    full_name = serializers.CharField(read_only=True)


class AdminSubscriptionSerializer(serializers.ModelSerializer):
    user = AdminUserBriefSerializer(read_only=True)
    plan_name = serializers.CharField(source="plan.name", read_only=True, default=None)
    remaining_days = serializers.IntegerField(read_only=True)
    remaining_volume_gb = serializers.FloatField(read_only=True, allow_null=True)
    is_unlimited_volume = serializers.BooleanField(read_only=True)
    is_unlimited_users = serializers.BooleanField(read_only=True)

    class Meta:
        model = UserVpnSubscription
        fields = [
            "id", "user", "label", "source", "plan_name", "status",
            "volume_gb", "duration_days", "max_concurrent_users",
            "remaining_days", "remaining_volume_gb",
            "is_unlimited_volume", "is_unlimited_users",
            "price", "used_traffic_bytes",
            "started_at", "expires_at", "last_synced_at",
            "xui_client_email", "xui_client_subid", "subscription_link",
            "hidden_at", "created_at",
        ]
        read_only_fields = fields


class AdminPaymentProofSerializer(serializers.ModelSerializer):
    user = AdminUserBriefSerializer(source="subscription.user", read_only=True)
    subscription_id = serializers.UUIDField(read_only=True)
    receipt_image_url = serializers.SerializerMethodField()
    plan_summary = serializers.SerializerMethodField()

    class Meta:
        model = PaymentProof
        fields = [
            "id", "subscription_id", "user", "kind", "amount",
            "extra_days", "extra_gb",
            "plan_summary",
            "receipt_image_url", "receipt_text",
            "ai_checked", "ai_verdict", "ai_notes",
            "is_approved", "reviewed_at", "admin_note",
            "created_at",
        ]
        read_only_fields = fields

    def get_receipt_image_url(self, obj):
        if not obj.receipt_image:
            return None
        # Deliberately NOT obj.receipt_image.url - that would be a direct
        # MinIO link. Receipts go through PaymentReceiptView so access is
        # checked per request; the client must send its auth header.
        request = self.context.get("request")
        path = f"/api/v1/vpn/payment-proofs/{obj.id}/receipt/"
        return request.build_absolute_uri(path) if request else path

    def get_plan_summary(self, obj):
        sub = obj.subscription
        volume = "unlimited" if sub.is_unlimited_volume else f"{sub.volume_gb}GB"
        users = "unlimited" if sub.is_unlimited_users else str(sub.max_concurrent_users)
        return {
            "name": sub.plan.name if sub.plan else "Custom",
            "volume": volume,
            "duration_days": sub.duration_days,
            "users": users,
        }
