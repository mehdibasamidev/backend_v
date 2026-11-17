from django.db import transaction
from django.utils import timezone

from apps.referral.services.rewards import award_first_purchase, lock_purchase_referral, release_checkout_reward
from apps.vpn.models import PaymentProof, PaymentProofKindChoices
from apps.vpn.services.provisioning import (
    activate_subscription,
    reject_subscription,
    reset_subscription_period,
    subscription_period_used_up,
)
from config.utils.exceptions import BadRequestException


def _lock_pending_proof(proof):
    pending = PaymentProof.objects.select_related("subscription").select_for_update(of=("self",)).filter(
        pk=proof.pk, is_approved__isnull=True,
    ).first()
    if pending is None:
        raise BadRequestException("This payment has already been reviewed or removed.")
    return pending


@transaction.atomic
def approve_payment_proof(proof, reviewed_by=None, admin_note=""):
    """
    Single place that turns an approved receipt into a real change on the
    3x-ui panel - so the Django admin action, the API endpoint, and the
    Telegram bot button all behave identically.

    A PURCHASE proof activates the subscription (creates the panel client).
    A RENEWAL proof resets the client to exactly the renewed amounts - right
    away if the current period has run out (volume or days), otherwise it is
    QUEUED: approved now, applied by the bot container's monitor
    (apply_queued_renewal) the moment that period ends, so the customer
    still gets to use what they already paid for.
    """
    referral = lock_purchase_referral(proof)
    proof = _lock_pending_proof(proof)
    # The panel call comes before approval. Marking the proof approved and then
    # failing to provision would strand it: is_approved is no longer null,
    # so the review endpoint refuses to touch it again and the admin has no
    # way to retry - while the customer has paid and has nothing.
    applied_at = timezone.now()
    if proof.kind == PaymentProofKindChoices.RENEWAL:
        subscription = proof.subscription
        if subscription_period_used_up(subscription):
            reset_subscription_period(subscription, proof.extra_days, proof.extra_gb)
        else:
            applied_at = None
    else:
        subscription = activate_subscription(proof.subscription)

    proof.is_approved = True
    proof.reviewed_by = reviewed_by
    proof.reviewed_at = timezone.now()
    proof.applied_at = applied_at
    if admin_note:
        proof.admin_note = admin_note
    proof.save(update_fields=["is_approved", "reviewed_by", "reviewed_at", "applied_at", "admin_note"])
    award_first_purchase(proof, referral)

    return subscription


def apply_queued_renewal(proof_id):
    """
    Starts a queued renewal if its subscription's current period is over.
    Returns the subscription when it was applied, None otherwise.

    Claimed with a conditional UPDATE so two bot containers (or two ticks)
    can't both reset the client; the claim is undone if the panel call
    fails, so the next tick retries.
    """
    proof = (
        PaymentProof.objects.select_related("subscription")
        .filter(id=proof_id, kind=PaymentProofKindChoices.RENEWAL, is_approved=True, applied_at__isnull=True)
        .first()
    )
    if proof is None or not subscription_period_used_up(proof.subscription):
        return None

    claimed_at = timezone.now()
    won = PaymentProof.objects.filter(id=proof.id, applied_at__isnull=True).update(applied_at=claimed_at)
    if won != 1:
        return None
    try:
        return reset_subscription_period(proof.subscription, proof.extra_days, proof.extra_gb)
    except Exception:
        with transaction.atomic():
            PaymentProof.objects.filter(id=proof.id, applied_at=claimed_at).update(applied_at=None)
        raise


@transaction.atomic
def reject_payment_proof(proof, reviewed_by=None, admin_note=""):
    """
    Rejecting a renewal must NOT kill the subscription - the user still
    has whatever they already paid for. Only a rejected initial purchase
    marks the subscription itself as rejected.
    """
    proof = _lock_pending_proof(proof)
    proof.is_approved = False
    proof.reviewed_by = reviewed_by
    proof.reviewed_at = timezone.now()
    if admin_note:
        proof.admin_note = admin_note
    proof.save(update_fields=["is_approved", "reviewed_by", "reviewed_at", "admin_note"])
    release_checkout_reward(proof)

    if proof.kind == PaymentProofKindChoices.PURCHASE:
        return reject_subscription(proof.subscription)
    return proof.subscription
