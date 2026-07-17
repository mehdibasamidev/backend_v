from rest_framework import serializers

from apps.vpn.models import UserVpnSubscription


class UserVpnSubscriptionSerializer(serializers.ModelSerializer):
    remaining_days = serializers.IntegerField(read_only=True)
    remaining_volume_gb = serializers.FloatField(read_only=True, allow_null=True)
    is_unlimited_volume = serializers.BooleanField(read_only=True)
    is_unlimited_users = serializers.BooleanField(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    payment_status = serializers.SerializerMethodField()

    class Meta:
        model = UserVpnSubscription
        fields = [
            "id", "label", "source", "status",
            "volume_gb", "duration_days", "max_concurrent_users",
            "remaining_days", "remaining_volume_gb",
            "is_unlimited_volume", "is_unlimited_users", "is_expired",
            "subscription_link", "started_at", "expires_at",
            "price", "payment_status", "created_at",
        ]
        read_only_fields = fields

    def get_payment_status(self, obj):
        proof = getattr(obj, "payment_proof", None)
        if not proof:
            return "not_submitted"
        if proof.is_approved is True:
            return "approved"
        if proof.is_approved is False:
            return "rejected"
        return "pending_review"
