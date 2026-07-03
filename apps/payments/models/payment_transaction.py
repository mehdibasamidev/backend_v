import uuid
from django.db import models
from django.conf import settings


class PaymentTransaction(models.Model):
    PAYMENT_TYPE = (
        ('subscription', 'Subscription'),
        ('coach_service', 'Coach Service'),
    )

    STATUS = (
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='payments')

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default='USD')

    payment_type = models.CharField(max_length=20, choices=PAYMENT_TYPE)
    status = models.CharField(max_length=20, choices=STATUS, default='pending')

    provider = models.CharField(max_length=50, default='stripe')
    provider_payment_id = models.CharField(max_length=255, blank=True)

    metadata = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
