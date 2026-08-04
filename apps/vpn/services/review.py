from django.utils import timezone

from apps.vpn.models import PaymentProofKindChoices
from apps.vpn.services.provisioning import (
    activate_subscription,
    extend_subscription,
    reject_subscription,
)


def approve_payment_proof(proof, reviewed_by=None, admin_note=""):
    """
    Single place that turns an approved receipt into a real change on the
    3x-ui panel - so the Django admin action, the API endpoint, and the
    Telegram bot button all behave identically.

    A PURCHASE proof activates the subscription (creates the panel
    client); a RENEWAL proof extends the existing client instead.
    """
    # The panel call comes FIRST. Marking the proof approved and then
    # failing to provision would strand it: is_approved is no longer null,
    # so the review endpoint refuses to touch it again and the admin has no
    # way to retry - while the customer has paid and has nothing.
    if proof.kind == PaymentProofKindChoices.RENEWAL:
        subscription = extend_subscription(
            proof.subscription,
            extra_days=proof.extra_days,
            extra_gb=proof.extra_gb,
        )
    else:
        subscription = activate_subscription(proof.subscription)

    proof.is_approved = True
    proof.reviewed_by = reviewed_by
    proof.reviewed_at = timezone.now()
    if admin_note:
        proof.admin_note = admin_note
    proof.save(update_fields=["is_approved", "reviewed_by", "reviewed_at", "admin_note"])

    return subscription


def reject_payment_proof(proof, reviewed_by=None, admin_note=""):
    """
    Rejecting a renewal must NOT kill the subscription - the user still
    has whatever they already paid for. Only a rejected initial purchase
    marks the subscription itself as rejected.
    """
    proof.is_approved = False
    proof.reviewed_by = reviewed_by
    proof.reviewed_at = timezone.now()
    if admin_note:
        proof.admin_note = admin_note
    proof.save(update_fields=["is_approved", "reviewed_by", "reviewed_at", "admin_note"])

    if proof.kind == PaymentProofKindChoices.PURCHASE:
        return reject_subscription(proof.subscription)
    return proof.subscription
