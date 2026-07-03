from django.shortcuts import get_object_or_404
from apps.payments.models.payment_transaction import PaymentTransaction


class PaymentTransactionService:

    @staticmethod
    def create_transaction(user, amount, payment_type, metadata=None, currency='USD', provider='stripe'):
        """Create a new payment transaction"""
        transaction = PaymentTransaction.objects.create(
            user=user,
            amount=amount,
            payment_type=payment_type,
            currency=currency,
            metadata=metadata or {},
            provider=provider
        )
        return transaction

    @staticmethod
    def mark_paid(transaction_id, metadata=None):
        transaction = get_object_or_404(PaymentTransaction, id=transaction_id)
        transaction.status = 'paid'
        if metadata:
            transaction.metadata.update(metadata)
        transaction.save()
        return transaction

    @staticmethod
    def mark_failed(transaction_id):
        transaction = get_object_or_404(PaymentTransaction, id=transaction_id)
        transaction.status = 'failed'
        transaction.save()
        return transaction
