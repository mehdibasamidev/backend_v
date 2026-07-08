from rest_framework import serializers

from apps.vpn.models import PaymentProof


class PaymentProofSubmitSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentProof
        fields = ["receipt_image", "receipt_text"]

    def validate(self, attrs):
        if not attrs.get("receipt_image") and not attrs.get("receipt_text"):
            raise serializers.ValidationError(
                "Provide at least a receipt image or a text reference (e.g. transaction id)."
            )
        return attrs
