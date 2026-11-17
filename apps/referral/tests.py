from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from rest_framework.test import APIClient

from apps.bot.services.account_merge import merge_bot_account_into
from apps.referral.models import CheckoutRewardUse, ReferralCode, ReferralCodeKind, ReferralReward, ReferralSettings, RewardKind
from apps.referral.services.redemption import redeem
from apps.referral.services.rewards import available_discounts, wallet_balance
from apps.vpn.models import PaymentProofSourceChoices, UserVpnSubscription, VpnPlan
from apps.vpn.services.checkout import create_paid_order
from apps.vpn.services.review import approve_payment_proof, reject_payment_proof
from config.utils.exceptions import BadRequestException


class ReferralWalletTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.inviter = User.objects.create_user(identifier="inviter", username="inviter")
        self.friend = User.objects.create_user(identifier="friend", username="friend")
        self.code = ReferralCode.objects.create(
            code="INVITE01", kind=ReferralCodeKind.PERSONAL, owner=self.inviter,
        )
        self.referral = redeem(self.code.code, self.friend)
        self.plan = VpnPlan.objects.create(name="Test", volume_gb=10, duration_days=30, price=Decimal("10000"))
        self.policy = ReferralSettings.get_solo()
        self.policy.reward_kind = RewardKind.WALLET
        self.policy.reward_value = Decimal("3000")
        self.policy.save()
        self.api = APIClient()

    def _order(self, user=None, **kwargs):
        return create_paid_order(user=user or self.friend, plan_id=self.plan.pk, receipt_text="Paid", **kwargs)[1]

    def _approve(self, proof):
        with patch("apps.vpn.services.review.activate_subscription", side_effect=lambda subscription: subscription):
            approve_payment_proof(proof)

    def _quote(self):
        self.api.force_authenticate(self.inviter)
        response = self.api.post("/api/v1/vpn/checkout/quote/", {"plan_id": self.plan.pk}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["data"]

    def _checkout(self, quote, reward_kind, due, **kwargs):
        return self.api.post("/api/v1/vpn/checkout/", {
            "plan_id": str(self.plan.pk), "reward_kind": reward_kind,
            "expected_price": quote["price"], "expected_amount": due,
            "request_id": quote["request_id"], **kwargs,
        }, format="multipart")

    def test_only_first_successful_app_purchase_earns_credit(self):
        reject_payment_proof(self._order())
        self._approve(self._order(source=PaymentProofSourceChoices.BOT))
        unpaid = self._order()
        with patch("apps.vpn.services.review.activate_subscription", side_effect=BadRequestException("Panel offline")):
            with self.assertRaises(BadRequestException):
                approve_payment_proof(unpaid)
        unpaid.refresh_from_db()
        self.assertIsNone(unpaid.is_approved)
        self.assertEqual(wallet_balance(self.inviter), 0)
        self._approve(unpaid)
        self._approve(self._order())
        with self.assertRaises(BadRequestException):
            self._approve(unpaid)

        self.referral.refresh_from_db()
        self.assertTrue(self.referral.is_rewarded)
        self.assertEqual(wallet_balance(self.inviter), 3000)
        self.assertEqual(ReferralReward.objects.get().purchase_id, unpaid.pk)

    def test_wallet_reservation_retries_once_and_releases_on_rejection(self):
        self._approve(self._order())
        quote = self._quote()
        self.assertEqual(Decimal(quote["wallet_amount"]), 3000)
        count = UserVpnSubscription.objects.count()

        stale = self._checkout(quote, RewardKind.WALLET, "5000", receipt_text="Invalid amount")
        self.assertEqual(stale.status_code, 400)
        missing_receipt = self._checkout(quote, RewardKind.WALLET, "7000")
        self.assertEqual(missing_receipt.status_code, 400)
        self.assertEqual(UserVpnSubscription.objects.count(), count)

        order = self._checkout(quote, RewardKind.WALLET, "7000", receipt_text="Reference")
        self.assertEqual(order.status_code, 200, order.data)
        duplicate = self._checkout(quote, RewardKind.WALLET, "7000", receipt_text="Reference")
        self.assertEqual(duplicate.data["data"]["id"], order.data["data"]["id"])
        self.assertEqual(UserVpnSubscription.objects.count(), count + 1)
        self.assertEqual(wallet_balance(self.inviter), 0)

        summary = self.api.get("/api/v1/referral/wallet/").data["data"]
        self.assertEqual(Decimal(summary["reserved"]), 3000)
        use = CheckoutRewardUse.objects.get()
        self.assertEqual(use.proof.amount, 7000)
        reject_payment_proof(use.proof)
        self.assertEqual(wallet_balance(self.inviter), 3000)

    def test_discount_is_reusable_after_rejection_and_spent_after_approval(self):
        self.policy.reward_kind = RewardKind.DISCOUNT
        self.policy.reward_value = Decimal("20")
        self.policy.save()
        self._approve(self._order())
        quote = self._quote()
        self.assertEqual(Decimal(quote["discount_percent"]), 20)
        self.assertEqual(Decimal(quote["discount_amount"]), 2000)
        order = self._checkout(quote, RewardKind.DISCOUNT, "8000", receipt_text="Reference")
        self.assertEqual(order.status_code, 200, order.data)
        self.assertFalse(available_discounts(self.inviter).exists())
        reject_payment_proof(CheckoutRewardUse.objects.get(request_id=quote["request_id"]).proof)
        quote = self._quote()
        order = self._checkout(quote, RewardKind.DISCOUNT, "8000", receipt_text="Reference")
        self.assertEqual(order.status_code, 200, order.data)
        self._approve(CheckoutRewardUse.objects.get(request_id=quote["request_id"]).proof)
        self.assertFalse(available_discounts(self.inviter).exists())

    def test_wallet_only_purchase_has_no_receipt_and_reward_follows_account_merge(self):
        self.policy.reward_value = Decimal("12000")
        self.policy.save()
        self._approve(self._order())
        quote = self._quote()
        order = self._checkout(quote, RewardKind.WALLET, "0")
        self.assertEqual(order.status_code, 200, order.data)
        use = CheckoutRewardUse.objects.get()
        self.assertEqual(use.proof.amount, 0)
        self.assertFalse(use.proof.receipt_text)
        self.assertEqual(wallet_balance(self.inviter), 2000)
        target = get_user_model().objects.create_user(
            identifier="target", username="target", email="target@example.com",
        )
        merge_bot_account_into(self.inviter, target)
        self.assertEqual(wallet_balance(target), 2000)
        self.assertEqual(wallet_balance(self.inviter), 0)
        reject_payment_proof(use.proof)
        self.assertEqual(wallet_balance(target), 12000)

    def test_success_when_rewards_disabled_is_not_rewarded_later(self):
        self.policy.reward_kind = RewardKind.DISABLED
        self.policy.save()
        self._approve(self._order())
        self.policy.reward_kind = RewardKind.WALLET
        self.policy.save()
        self._approve(self._order())
        self.assertFalse(ReferralReward.objects.exists())


class ReferralWalletConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_two_concurrent_orders_cannot_spend_the_same_credit(self):
        User = get_user_model()
        inviter = User.objects.create_user(identifier="inviter", username="inviter")
        friend = User.objects.create_user(identifier="friend", username="friend")
        code = ReferralCode.objects.create(code="INVITE02", kind=ReferralCodeKind.PERSONAL, owner=inviter)
        redeem(code.code, friend)
        plan = VpnPlan.objects.create(name="Test", volume_gb=10, duration_days=30, price=Decimal("10000"))
        policy = ReferralSettings.get_solo()
        policy.reward_kind = RewardKind.WALLET
        policy.reward_value = plan.price
        policy.save()
        _, proof = create_paid_order(user=friend, plan_id=plan.pk, receipt_text="Paid")
        with patch("apps.vpn.services.review.activate_subscription", side_effect=lambda subscription: subscription):
            approve_payment_proof(proof)

        ready = Barrier(2, timeout=5)

        def attempt():
            try:
                ready.wait()
                create_paid_order(
                    user=inviter, plan_id=plan.pk, reward_kind=RewardKind.WALLET,
                    expected_price=plan.price, expected_amount=Decimal("0"), request_id=uuid4(),
                )
            except BadRequestException:
                return False
            finally:
                connections.close_all()
            return True

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: attempt(), range(2)))

        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(wallet_balance(inviter), 0)
        self.assertEqual(CheckoutRewardUse.objects.count(), 1)
