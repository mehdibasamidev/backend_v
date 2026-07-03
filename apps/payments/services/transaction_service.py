from django.shortcuts import get_object_or_404
from apps.payments.models.coach_service import CoachRequest
from apps.payments.models.payment_transaction import PaymentTransaction
from apps.payments.models.revenue_split import RevenueSplit
from decimal import Decimal


class PaymentTransactionService:

    @staticmethod
    def create_payment(user, *, amount, payment_type, metadata=None):
        """Create a pending payment transaction"""
        if metadata is None:
            metadata = {}
        payment = PaymentTransaction.objects.create(
            user=user,
            amount=amount,
            payment_type=payment_type,
            status='pending',
            metadata=metadata
        )
        return payment

    @staticmethod
    def handle_successful_payment(payment_id: str):
        """Mark transaction as paid and handle revenue split"""
        payment = get_object_or_404(PaymentTransaction, id=payment_id)
        if payment.status != 'pending':
            return payment

        payment.status = 'paid'
        payment.save()

        # Revenue split for coach service
        if payment.payment_type == 'coach_service':
            coach_id = payment.metadata.get('coach_id')
            platform_percent = Decimal(payment.metadata.get('platform_percent', 20))
            coach_amount = payment.amount * (Decimal(100) - platform_percent) / 100
            platform_amount = payment.amount - coach_amount
            RevenueSplit.objects.create(
                transaction=payment,
                coach_id=coach_id,
                coach_amount=coach_amount,
                platform_amount=platform_amount
            )

            # Mark coach request paid
            request_id = payment.metadata.get('coach_request_id')
            if request_id:
                request = CoachRequest.objects.get(id=request_id)
                request.status = 'paid'
                request.payment = payment
                request.save()

        # Subscription payments handled elsewhere
        return payment
