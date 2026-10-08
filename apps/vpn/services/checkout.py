from django.db import transaction

from apps.vpn.models import (
    VpnPlan,
    UserVpnSubscription,
    PaymentProof,
    PaymentProofKindChoices,
    PaymentProofSourceChoices,
    PlanSourceChoices,
    SubscriptionStatusChoices,
)
from apps.vpn.services.pricing import (
    RenewalUnavailable,
    calculate_custom_plan_price,
    renewal_quote,
)
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
    source=PaymentProofSourceChoices.APP,
):
    """
    Creates a subscription together with its payment proof, in one atomic
    step. Nothing is written until the user actually has a receipt to
    show - browsing the plan list leaves no trace.

    Price is always recalculated here, server-side; whatever the client
    displayed while the user was choosing was only a preview.

    `source` is where the receipt came from (app or bot), stored on the
    proof for the admin announcement.

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
            purchased_volume_gb=plan.volume_gb,
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
            purchased_volume_gb=volume_gb,
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
        source=source,
    )
    return subscription, proof


@transaction.atomic
def create_renewal_order(
    *,
    user,
    subscription_id,
    receipt_image=None,
    receipt_text="",
    source=PaymentProofSourceChoices.APP,
):
    """
    Records a renewal receipt for the bot and the REST API alike - the
    customer never picks days or GB; renewal_quote decides both and the
    price. Approval resets the service (see review.approve_payment_proof).
    """
    subscription, extra_days, extra_gb, price = default_renewal_quote(
        user=user, subscription_id=subscription_id, lock=True,
    )
    proof = PaymentProof.objects.create(
        subscription=subscription,
        kind=PaymentProofKindChoices.RENEWAL,
        amount=price,
        extra_days=extra_days,
        extra_gb=extra_gb,
        receipt_image=receipt_image,
        receipt_text=receipt_text,
        source=source,
    )
    return subscription, proof


def queued_renewal(subscription):
    """An approved renewal still waiting for the current period to run out."""
    return (
        subscription.payment_proofs
        .filter(kind=PaymentProofKindChoices.RENEWAL, is_approved=True, applied_at__isnull=True)
        .order_by("reviewed_at")
        .first()
    )


def current_period_used_up(subscription):
    """
    From our synced copy: has the current period run out (volume OR days)?
    Only a preview for the customer - approval decides from the panel's
    live numbers (provisioning.panel_period_used_up).
    """
    if subscription.is_expired:
        return True
    if subscription.is_unlimited_volume:
        return False
    return (subscription.remaining_volume_gb or 0) <= 0


def _check_renewable(subscription):
    if not subscription.xui_client_email:
        raise RenewalUnavailable(
            "not_activated",
            "This subscription hasn't been activated yet, so it can't be renewed",
        )
    if subscription.payment_proofs.filter(is_approved__isnull=True).exists():
        raise RenewalUnavailable(
            "pending_review",
            "You already have a payment awaiting review for this subscription",
        )
    if queued_renewal(subscription) is not None:
        # One queued period at a time: a second would have to queue behind
        # the first, and stacking prepaid periods is what this rule avoids.
        raise RenewalUnavailable(
            "already_queued",
            "A renewal is already paid for and starts when the current period ends",
        )


def default_renewal_quote(*, user, subscription_id, lock=False):
    """
    (subscription, days, gb, price) for renewing this customer's service.

    lock=True (inside a transaction) holds the subscription row until the
    proof is written, so two receipts sent back to back can't both pass the
    "already awaiting review" check.
    """
    queryset = UserVpnSubscription.objects.select_related("plan")
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    try:
        subscription = queryset.get(id=subscription_id, user=user)
    except UserVpnSubscription.DoesNotExist as exc:
        raise BadRequestException("Subscription not found") from exc

    _check_renewable(subscription)
    extra_days, extra_gb, price = renewal_quote(subscription)
    return subscription, extra_days, extra_gb, price


def renewal_info(subscription):
    """
    Everything a client needs to show the renew option - one shape for the
    REST serializer and the bot. Never raises.
    """
    info = {
        "available": True,
        "unavailable_reason": None,
        "days": subscription.duration_days,
        "volume_gb": subscription.purchased_volume_gb
        if subscription.purchased_volume_gb is not None else subscription.volume_gb,
        "price": None,
        "starts": "now" if current_period_used_up(subscription) else "after_current",
        "queued_renewal": None,
    }
    queued = queued_renewal(subscription)
    if queued is not None:
        info["queued_renewal"] = {"days": queued.extra_days, "volume_gb": queued.extra_gb}
    try:
        _check_renewable(subscription)
        info["days"], info["volume_gb"], info["price"] = renewal_quote(subscription)
    except RenewalUnavailable as exc:
        info["available"] = False
        info["unavailable_reason"] = exc.reason
    return info
