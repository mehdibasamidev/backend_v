from decimal import Decimal, ROUND_DOWN

from django.contrib.auth import get_user_model
from django.db.models import Q, Sum
from django.utils import timezone

from apps.referral.models import CheckoutRewardUse, Referral, ReferralReward, ReferralSettings, RewardKind
from apps.vpn.models import PaymentProof, PaymentProofKindChoices, PaymentProofSourceChoices
from config.utils.exceptions import BadRequestException


ZERO = Decimal("0.00")


def lock_reward_users(*user_ids):
    # Checkout, approval and Telegram account merge share the same lock
    # order, so credits and reservations always follow the current owner.
    return list(
        get_user_model().objects.select_for_update(no_key=True).filter(pk__in=user_ids).order_by("pk")
    )


def _purchase_referral(buyer_id):
    return (
        Referral.objects.filter(referred_user_id=buyer_id, referrer__isnull=False)
        .exclude(referrer_id=buyer_id)
        .first()
    )


def lock_purchase_referral(proof):
    if (
        proof.kind != PaymentProofKindChoices.PURCHASE
        or proof.source != PaymentProofSourceChoices.APP
        or proof.amount == ZERO
    ):
        return None

    buyer_id = proof.subscription.user_id
    referral = _purchase_referral(buyer_id)
    account_ids = [buyer_id]
    if referral:
        account_ids.append(referral.referrer_id)
    users = {user.pk: user for user in lock_reward_users(*account_ids)}

    # A Telegram merge might have finished while we waited for those rows.
    current = _purchase_referral(buyer_id)
    if (
        not PaymentProof.objects.filter(pk=proof.pk, subscription__user_id=buyer_id).exists()
        or (current and (not referral or current.referrer_id != referral.referrer_id))
    ):
        raise BadRequestException("The buyer's referral account changed. Review the payment again.")
    if current and users[current.referrer_id].is_active:
        return current
    return None


def award_first_purchase(proof, referral):
    if referral is None or referral.is_rewarded:
        return
    policy = ReferralSettings.get_solo()
    if policy.reward_kind == RewardKind.DISABLED:
        return
    amount = proof.amount if proof.amount is not None else proof.subscription.price
    if amount <= ZERO:
        return

    # Include legacy receipts whose amount lived only on the subscription.
    # Wallet-only orders cannot earn new credit for someone else.
    earlier_purchase = PaymentProof.objects.filter(
        subscription__user_id=referral.referred_user_id,
        kind=PaymentProofKindChoices.PURCHASE,
        source=PaymentProofSourceChoices.APP,
        is_approved=True,
    ).filter(Q(amount__gt=ZERO) | Q(amount__isnull=True, subscription__price__gt=ZERO))
    if earlier_purchase.exclude(pk=proof.pk).exists():
        return

    ReferralReward.objects.create(
        referral=referral, purchase=proof, user_id=referral.referrer_id,
        kind=policy.reward_kind, value=policy.reward_value,
    )
    referral.is_rewarded = True
    referral.rewarded_at = timezone.now()
    referral.save(update_fields=["is_rewarded", "rewarded_at"])


def wallet_balance(user):
    credits = ReferralReward.objects.filter(user=user, kind=RewardKind.WALLET).aggregate(
        total=Sum("value", default=ZERO),
    )["total"]
    reserved_or_spent = CheckoutRewardUse.objects.filter(user=user, released_at__isnull=True).aggregate(
        total=Sum("wallet_amount", default=ZERO),
    )["total"]
    return credits - reserved_or_spent


def available_discounts(user):
    reserved = CheckoutRewardUse.objects.filter(released_at__isnull=True, discount__isnull=False)
    return ReferralReward.objects.filter(user=user, kind=RewardKind.DISCOUNT).exclude(
        pk__in=reserved.values("discount_id"),
    ).order_by("-value", "created_at", "pk")


def wallet_summary(user):
    discounts = list(available_discounts(user).values_list("value", flat=True))
    pending = CheckoutRewardUse.objects.filter(
        user=user, released_at__isnull=True, proof__is_approved__isnull=True,
    ).aggregate(total=Sum("wallet_amount", default=ZERO))["total"]
    return {
        "balance": str(wallet_balance(user)),
        "reserved": str(pending),
        "available_discounts": [str(percent) for percent in discounts],
        "recent_rewards": [
            {"kind": reward.kind, "value": str(reward.value), "created_at": reward.created_at}
            for reward in ReferralReward.objects.filter(user=user)[:20]
        ],
    }


def quote_checkout_rewards(user, price):
    wallet_amount = max(ZERO, min(price, wallet_balance(user)))
    discount = available_discounts(user).first()
    discount_amount = (
        (price * discount.value / 100).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if discount else ZERO
    )
    return wallet_amount, discount, discount_amount


def release_checkout_reward(proof):
    CheckoutRewardUse.objects.filter(proof=proof, released_at__isnull=True).update(released_at=timezone.now())
