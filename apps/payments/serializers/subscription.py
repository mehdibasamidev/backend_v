from rest_framework import serializers
from apps.payments.models.subscription import SubscriptionPlan, Subscription
from apps.payments.serializers.payment_transaction import PaymentTransactionSerializer


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = [
            'id', 'title', 'description', 'price', 'duration_days', 'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class SubscriptionSerializer(serializers.ModelSerializer):
    plan = SubscriptionPlanSerializer(read_only=True)
    last_payment = PaymentTransactionSerializer(read_only=True)

    class Meta:
        model = Subscription
        fields = [
            'id', 'user', 'plan', 'status', 'start_date', 'end_date', 'last_payment', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'user', 'status', 'last_payment', 'created_at', 'updated_at']
