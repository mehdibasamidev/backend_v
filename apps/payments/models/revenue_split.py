import uuid
from django.db import models
from django.conf import settings
from apps.payments.models.payment_transaction import PaymentTransaction


class RevenueSplit(models.Model):
    SPLIT_TYPE_CHOICES = [
        ('coach', 'Coach'),
        ('platform', 'Platform'),
        ('ai', 'AI'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transaction = models.ForeignKey(PaymentTransaction, on_delete=models.CASCADE, related_name='splits')
    beneficiary = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='revenue_splits')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    split_type = models.CharField(max_length=20, choices=SPLIT_TYPE_CHOICES)
    percentage = models.DecimalField(max_digits=5, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Revenue Split'
        verbose_name_plural = 'Revenue Splits'

    def __str__(self):
        return f"{self.split_type}: {self.beneficiary} - {self.amount}"
