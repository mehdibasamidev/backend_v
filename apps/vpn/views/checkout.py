import uuid

from rest_framework import serializers
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.renderers import JSONRenderer
from drf_yasg.utils import swagger_auto_schema

from apps.referral.models import RewardKind
from apps.referral.services.rewards import quote_checkout_rewards
from apps.vpn.models import PaymentProofSourceChoices
from apps.vpn.serializers.subscriptions import UserVpnSubscriptionSerializer
from apps.vpn.services.ai_receipt import analyze_payment_receipt
from apps.vpn.services.checkout import (
    create_paid_order,
    create_renewal_order,
    current_period_used_up,
    purchase_plan_and_price,
)
from config.utils.custom_serializers import create_response_serializer
from config.utils.exceptions import AppException
from config.utils.response import (
    SuccessResponse,
    BadRequestResponse,
    ServerErrorResponse,
)


class PurchaseSelectionSerializer(serializers.Serializer):
    plan_id = serializers.UUIDField(required=False)

    volume_gb = serializers.IntegerField(required=False, min_value=0)
    duration_days = serializers.IntegerField(required=False, min_value=1)
    max_concurrent_users = serializers.IntegerField(required=False, min_value=0)

    def validate(self, attrs):
        is_custom = attrs.get("plan_id") is None

        if is_custom:
            missing = [
                f for f in ("volume_gb", "duration_days", "max_concurrent_users")
                if attrs.get(f) is None
            ]
            if missing:
                raise serializers.ValidationError(
                    f"For a custom plan these fields are required: {', '.join(missing)}"
                )

        return attrs


class CheckoutSerializer(PurchaseSelectionSerializer):
    label = serializers.CharField(required=False, allow_blank=True, max_length=100)
    receipt_image = serializers.FileField(required=False)
    receipt_text = serializers.CharField(required=False, allow_blank=True)
    reward_kind = serializers.ChoiceField(choices=RewardKind.choices, default=RewardKind.DISABLED)
    expected_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, min_value=0)
    expected_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, min_value=0)
    request_id = serializers.UUIDField(required=False)


class CheckoutQuoteView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]

    @swagger_auto_schema(request_body=PurchaseSelectionSerializer)
    def post(self, request):
        serializer = PurchaseSelectionSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)
        _, price = purchase_plan_and_price(**serializer.validated_data)
        wallet_amount, discount, discount_amount = quote_checkout_rewards(request.user, price)
        return SuccessResponse(data={
            "request_id": str(uuid.uuid4()),
            "price": str(price),
            "wallet_amount": str(wallet_amount),
            "discount_percent": str(discount.value) if discount_amount else None,
            "discount_amount": str(discount_amount),
        })


class CheckoutView(APIView):
    """
    Creates the subscription AND its payment proof in a single atomic
    transaction. The price is always (re)calculated server-side at this
    moment - anything the client displayed earlier was only a preview.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]
    renderer_classes = [JSONRenderer]

    @swagger_auto_schema(
        request_body=CheckoutSerializer,
        responses={201: create_response_serializer(
            data_serializer_class=UserVpnSubscriptionSerializer,
            text_message="Order submitted, awaiting admin approval",
        )},
    )
    def post(self, request):
        serializer = CheckoutSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        data = serializer.validated_data

        try:
            subscription, proof = create_paid_order(
                user=request.user,
                plan_id=data.get("plan_id"),
                volume_gb=data.get("volume_gb"),
                duration_days=data.get("duration_days"),
                max_concurrent_users=data.get("max_concurrent_users"),
                label=data.get("label", ""),
                receipt_image=data.get("receipt_image"),
                receipt_text=data.get("receipt_text", ""),
                source=PaymentProofSourceChoices.APP,
                reward_kind=data.get("reward_kind", RewardKind.DISABLED),
                expected_price=data.get("expected_price"),
                expected_amount=data.get("expected_amount"),
                request_id=data.get("request_id"),
            )
        except AppException as e:
            return BadRequestResponse(message=e.message)
        except Exception as e:
            return ServerErrorResponse(errors=str(e))

        # Best-effort only, and deliberately outside the transaction so a
        # slow/failing AI call can never roll back a real order.
        # The bot container announces this proof to the admins (outbox).
        try:
            if not proof.ai_checked:
                analyze_payment_receipt(proof)
        except Exception:
            pass

        return SuccessResponse(
            data=UserVpnSubscriptionSerializer(
                subscription, context={'request': request}
            ).data,
            message="Order submitted. An admin will review your payment shortly.",
        )


class RenewalSerializer(serializers.Serializer):
    """
    Only the receipt. What a renewal buys and costs is not the customer's
    choice: renewal_quote decides both (the service's own plan at today's
    price). Older app builds still send periods/extra_days/extra_gb; they
    are ignored rather than rejected so those builds keep working.
    """
    receipt_image = serializers.FileField(required=False)
    receipt_text = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs.get("receipt_image") and not attrs.get("receipt_text"):
            raise serializers.ValidationError(
                "Provide at least a receipt image or a text reference (e.g. transaction id)."
            )
        return attrs


class RenewSubscriptionView(APIView):
    """
    Renews a service: same manual-payment flow as a purchase, through the
    same service the Telegram bot uses. An admin approves; the service is
    then reset to exactly the plan's GB and days - immediately if the
    current period has run out, otherwise as soon as it does.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]
    renderer_classes = [JSONRenderer]

    @swagger_auto_schema(request_body=RenewalSerializer)
    def post(self, request, subscription_id):
        serializer = RenewalSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)
        data = serializer.validated_data

        try:
            subscription, proof = create_renewal_order(
                user=request.user,
                subscription_id=subscription_id,
                receipt_image=data.get("receipt_image"),
                receipt_text=data.get("receipt_text", ""),
                source=PaymentProofSourceChoices.APP,
            )
        except AppException as e:
            return BadRequestResponse(message=e.message)

        # The bot container announces this proof to the admins (outbox).
        try:
            analyze_payment_receipt(proof)
        except Exception:
            pass

        return SuccessResponse(
            data={
                "amount": str(proof.amount),
                "extra_days": proof.extra_days,
                "extra_gb": proof.extra_gb,
                "starts": "now" if current_period_used_up(subscription) else "after_current",
            },
            message="Renewal submitted. An admin will review your payment shortly.",
        )
