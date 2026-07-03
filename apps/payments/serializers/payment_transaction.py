from rest_framework import serializers
from apps.payments.models.payment_transaction import PaymentTransaction


class PaymentTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentTransaction
        fields = [
            'id', 'user', 'amount', 'currency', 'payment_type', 'status',
            'provider', 'provider_payment_id', 'metadata', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'user', 'status', 'provider_payment_id', 'created_at', 'updated_at']
