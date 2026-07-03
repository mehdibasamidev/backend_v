from django.shortcuts import get_object_or_404
from django.utils import timezone
from apps.payments.models.subscription import Subscription, SubscriptionPlan
from apps.payments.models.payment_transaction import PaymentTransaction


class SubscriptionService:

    @staticmethod
    def subscribe(user, plan_id, transaction: PaymentTransaction):
        """
        Activate a new subscription for a user or extend existing one.
        """
        plan = get_object_or_404(SubscriptionPlan, id=plan_id)

        # Check if user already has an active subscription for this plan
        existing = Subscription.objects.filter(user=user, plan=plan, status='active').first()
        now = timezone.now()

        if existing:
            # Extend subscription
            existing.end_date += timezone.timedelta(days=plan.duration_days)
            existing.last_payment = transaction
            existing.save()
            return existing

        # Create new subscription
        subscription = Subscription.objects.create(
            user=user,
            plan=plan,
            status='active',
            start_date=now,
            end_date=now + timezone.timedelta(days=plan.duration_days),
            last_payment=transaction
        )
        return subscription

    @staticmethod
    def cancel(subscription_id):
        subscription = get_object_or_404(Subscription, id=subscription_id)
        subscription.status = 'cancelled'
        subscription.save()
        return subscription

    @staticmethod
    def activate_or_extend(transaction: PaymentTransaction):
        """
        Helper for Stripe webhook: use transaction metadata to activate or extend subscription
        """
        plan_id = transaction.metadata.get('plan_id')
        if not plan_id:
            raise ValueError("Transaction metadata missing 'plan_id'")

        return SubscriptionService.subscribe(transaction.user, plan_id, transaction)
