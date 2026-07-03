# apps/payments/serializers/coach_request.py

from rest_framework import serializers
from apps.payments.models.coach_service import CoachService, CoachServiceRequest


class CoachServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = CoachService
        fields = ['id', 'title', 'description', 'price', 'duration_minutes', 'is_active']


class CoachServiceRequestSerializer(serializers.ModelSerializer):
    service = CoachServiceSerializer(read_only=True)
    service_id = serializers.PrimaryKeyRelatedField(
        queryset=CoachService.objects.filter(is_active=True),
        source='service',
        write_only=True
    )

    class Meta:
        model = CoachServiceRequest
        fields = ['id', 'service', 'service_id', 'status', 'created_at']
        read_only_fields = ['id', 'status', 'created_at']
