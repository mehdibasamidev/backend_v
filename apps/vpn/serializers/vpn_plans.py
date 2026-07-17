from rest_framework import serializers

from apps.vpn.models import VpnPlan, VpnPricingConfig
from apps.vpn.services.pricing import calculate_custom_plan_price


class VpnPlanSerializer(serializers.ModelSerializer):
    is_unlimited_volume = serializers.BooleanField(read_only=True)
    is_unlimited_users = serializers.BooleanField(read_only=True)

    class Meta:
        model = VpnPlan
        fields = [
            "id", "name", "description", "volume_gb", "duration_days",
            "max_concurrent_users", "price", "is_featured",
            "is_unlimited_volume", "is_unlimited_users",
        ]


class CustomPlanQuoteSerializer(serializers.Serializer):
    """
    Used both to preview a custom plan's price and to submit a purchase
    request for one. Price is always (re)calculated server-side.
    """
    volume_gb = serializers.IntegerField(min_value=1)
    duration_days = serializers.IntegerField(min_value=1)
    max_concurrent_users = serializers.IntegerField(min_value=1)
    label = serializers.CharField(required=False, allow_blank=True, max_length=100)

    def validate(self, attrs):
        # calculate_custom_plan_price re-validates ranges against the active
        # VpnPricingConfig and raises BadRequestException (caught in the view)
        # if anything is out of the allowed range.
        attrs["price"] = calculate_custom_plan_price(
            volume_gb=attrs["volume_gb"],
            duration_days=attrs["duration_days"],
            max_concurrent_users=attrs["max_concurrent_users"],
        )
        return attrs


class CustomPlanOptionsSerializer(serializers.ModelSerializer):
    """
    Exposes the allowed ranges/steps + unit prices so the frontend can build
    the "custom plan" picker (GB/day/user selects) and preview a price live.
    """
    class Meta:
        model = VpnPricingConfig
        fields = [
            "price_per_gb", "price_per_day", "price_per_extra_user", "base_price",
            "min_gb", "max_gb", "gb_step",
            "min_days", "max_days",
            "min_users", "max_users",
        ]
