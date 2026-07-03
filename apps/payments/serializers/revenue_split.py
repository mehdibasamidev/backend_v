from rest_framework import serializers
from apps.payments.models.revenue_split import RevenueSplit


class RevenueSplitSerializer(serializers.ModelSerializer):
    class Meta:
        model = RevenueSplit
        fields = [
            'id', 'transaction', 'beneficiary', 'amount', 'split_type', 'percentage', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']
