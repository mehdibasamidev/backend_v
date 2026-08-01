from rest_framework import serializers

from apps.vpn.models import UserVpnSubscription


class OwnPaymentProofSerializer(serializers.Serializer):
    """
    The customer's own view of their receipt - enough to show it back to
    them on an order detail screen and explain a rejection.

    Deliberately narrower than the admin serializer: no AI verdict, no
    reviewer identity.
    """
    id = serializers.UUIDField(read_only=True)
    kind = serializers.CharField(read_only=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    extra_days = serializers.IntegerField(read_only=True)
    extra_gb = serializers.IntegerField(read_only=True)
    receipt_text = serializers.CharField(read_only=True)
    is_approved = serializers.BooleanField(read_only=True, allow_null=True)
    admin_note = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    receipt_image_url = serializers.SerializerMethodField()

    def get_receipt_image_url(self, obj):
        if not obj.receipt_image:
            return None
        # Same permission-checked endpoint the admin panel uses -
        # PaymentReceiptView lets the owner through as well as staff.
        request = self.context.get("request")
        path = f"/api/v1/vpn/payment-proofs/{obj.id}/receipt/"
        return request.build_absolute_uri(path) if request else path


class UserVpnSubscriptionSerializer(serializers.ModelSerializer):
    remaining_days = serializers.IntegerField(read_only=True)
    remaining_volume_gb = serializers.FloatField(read_only=True, allow_null=True)
    is_unlimited_volume = serializers.BooleanField(read_only=True)
    is_unlimited_users = serializers.BooleanField(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    payment_status = serializers.SerializerMethodField()
    has_pending_payment = serializers.SerializerMethodField()
    plan_name = serializers.CharField(source="plan.name", read_only=True, default=None)
    latest_proof = serializers.SerializerMethodField()

    class Meta:
        model = UserVpnSubscription
        fields = [
            "id", "label", "source", "plan_name", "status",
            "volume_gb", "duration_days", "max_concurrent_users",
            "remaining_days", "remaining_volume_gb",
            "is_unlimited_volume", "is_unlimited_users", "is_expired",
            "subscription_link", "started_at", "expires_at",
            "price", "payment_status", "has_pending_payment",
            "latest_proof", "created_at",
        ]
        read_only_fields = fields

    def get_payment_status(self, obj):
        proof = obj.latest_payment_proof
        if not proof:
            return "not_submitted"
        if proof.is_approved is True:
            return "approved"
        if proof.is_approved is False:
            return "rejected"
        return "pending_review"

    def get_has_pending_payment(self, obj):
        return obj.payment_proofs.filter(is_approved__isnull=True).exists()

    def get_latest_proof(self, obj):
        proof = obj.latest_payment_proof
        if not proof:
            return None
        return OwnPaymentProofSerializer(proof, context=self.context).data
