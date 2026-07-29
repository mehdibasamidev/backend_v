from django.db import transaction

from apps.vpn.models import (
    VpnPlan,
    UserVpnSubscription,
    PaymentProof,
    PaymentProofKindChoices,
    PlanSourceChoices,
    SubscriptionStatusChoices,
)
from apps.vpn.services.pricing import calculate_custom_plan_price
from config.utils.exceptions import BadRequestException


@transaction.atomic
def create_paid_order(
    user,
    plan_id=None,
    volume_gb=None,
    duration_days=None,
    max_concurrent_users=None,
    label="",
    receipt_image=None,
    receipt_text="",
):
    """
    Creates a subscription together with its payment proof, in one atomic
    step. Nothing is written until the user actually has a receipt to
    show - browsing the plan list leaves no trace.

    Price is always recalculated here, server-side; whatever the client
    displayed while the user was choosing was only a preview.

    Returns (subscription, payment_proof).
    """
    if plan_id:
        try:
            plan = VpnPlan.objects.get(id=plan_id, is_active=True)
        except VpnPlan.DoesNotExist:
            raise BadRequestException("Selected plan was not found or is no longer available")

        subscription = UserVpnSubscription.objects.create(
            user=user,
            label=label,
            source=PlanSourceChoices.FIXED,
            plan=plan,
            volume_gb=plan.volume_gb,
            duration_days=plan.duration_days,
            max_concurrent_users=plan.max_concurrent_users,
            price=plan.price,
            status=SubscriptionStatusChoices.PENDING_APPROVAL,
        )
    else:
        price = calculate_custom_plan_price(
            volume_gb=volume_gb,
            duration_days=duration_days,
            max_concurrent_users=max_concurrent_users,
        )
        subscription = UserVpnSubscription.objects.create(
            user=user,
            label=label,
            source=PlanSourceChoices.CUSTOM,
            plan=None,
            volume_gb=volume_gb,
            duration_days=duration_days,
            max_concurrent_users=max_concurrent_users,
            price=price,
            status=SubscriptionStatusChoices.PENDING_APPROVAL,
        )

    proof = PaymentProof.objects.create(
        subscription=subscription,
        kind=PaymentProofKindChoices.PURCHASE,
        amount=subscription.price,
        receipt_image=receipt_image,
        receipt_text=receipt_text,
    )
    return subscription, proof
