from rest_framework import serializers
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser
from rest_framework.renderers import JSONRenderer
from drf_yasg.utils import swagger_auto_schema

from apps.vpn.models import (
    UserVpnSubscription,
    PaymentProof,
    PaymentProofKindChoices,
)
from apps.vpn.serializers.subscriptions import UserVpnSubscriptionSerializer
from apps.vpn.services.ai_receipt import analyze_payment_receipt
from apps.vpn.services.checkout import create_paid_order
from apps.vpn.services.pricing import resolve_renewal
from config.utils.custom_serializers import create_response_serializer
from config.utils.exceptions import AppException
from config.utils.response import (
    SuccessResponse,
    BadRequestResponse,
    ServerErrorResponse,
)


class CheckoutSerializer(serializers.Serializer):
    """
    One request that both picks the plan and submits the receipt, so we
    never create an orphan 'pending_payment' subscription for someone who
    just browsed the plan list and left.
    """
    plan_id = serializers.UUIDField(required=False)

    volume_gb = serializers.IntegerField(required=False, min_value=0)
    duration_days = serializers.IntegerField(required=False, min_value=1)
    max_concurrent_users = serializers.IntegerField(required=False, min_value=0)

    label = serializers.CharField(required=False, allow_blank=True, max_length=100)
    receipt_image = serializers.FileField(required=False)
    receipt_text = serializers.CharField(required=False, allow_blank=True)

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

        if not attrs.get("receipt_image") and not attrs.get("receipt_text"):
            raise serializers.ValidationError(
                "Provide at least a receipt image or a text reference (e.g. transaction id)."
            )
        return attrs


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
            )
        except AppException as e:
            return BadRequestResponse(message=e.message)
        except Exception as e:
            return ServerErrorResponse(errors=str(e))

        # Best-effort only, and deliberately outside the transaction so a
        # slow/failing AI call can never roll back a real order.
        try:
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
    Two shapes, depending on where the service came from:

      * fixed plan  -> `periods`: how many whole plan periods to add.
      * custom      -> `extra_days` / `extra_gb`.

    The client doesn't have to guess which: the subscription serializer
    exposes a `renewal.mode` field. Whatever arrives, the price is resolved
    server-side by resolve_renewal().
    """
    # Capped at two periods - a longer prepayment ties up a service the
    # customer may not want that far ahead, and the panel quota grows with
    # every renewal anyway.
    periods = serializers.IntegerField(min_value=1, max_value=2, required=False)
    extra_days = serializers.IntegerField(min_value=0, required=False)
    extra_gb = serializers.IntegerField(min_value=0, required=False)
    receipt_image = serializers.FileField(required=False)
    receipt_text = serializers.CharField(required=False, allow_blank=True)

    def __init__(self, *args, subscription=None, **kwargs):
        self.subscription = subscription
        super().__init__(*args, **kwargs)

    def validate_extra_gb(self, value):
        # An unlimited client has totalGB=0 on the panel, and bulkAdjust
        # explicitly skips the traffic field for those - selling volume
        # would take the money and change nothing.
        if value and self.subscription and self.subscription.is_unlimited_volume:
            raise serializers.ValidationError(
                "This service already has unlimited data, so extra volume "
                "cannot be added. Renew the duration instead."
            )
        return value

    def validate(self, attrs):
        if not attrs.get("receipt_image") and not attrs.get("receipt_text"):
            raise serializers.ValidationError(
                "Provide at least a receipt image or a text reference (e.g. transaction id)."
            )
        return attrs


class RenewSubscriptionView(APIView):
    """
    Tops up an existing subscription. Same manual-payment flow as a
    purchase: submit the receipt here, an admin approves, and only then is
    the panel client actually extended.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]
    renderer_classes = [JSONRenderer]

    @swagger_auto_schema(request_body=RenewalSerializer)
    def post(self, request, subscription_id):
        try:
            subscription = UserVpnSubscription.objects.select_related("plan").get(
                id=subscription_id, user=request.user,
            )
        except UserVpnSubscription.DoesNotExist:
            return BadRequestResponse(message="Subscription not found")

        if not subscription.xui_client_email:
            return BadRequestResponse(
                message="This subscription hasn't been activated yet, so it can't be renewed"
            )

        if subscription.payment_proofs.filter(is_approved__isnull=True).exists():
            return BadRequestResponse(
                message="You already have a payment awaiting review for this subscription"
            )

        serializer = RenewalSerializer(data=request.data, subscription=subscription)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        data = serializer.validated_data

        try:
            extra_days, extra_gb, price = resolve_renewal(
                subscription,
                periods=data.get("periods"),
                extra_days=data.get("extra_days"),
                extra_gb=data.get("extra_gb"),
            )
        except AppException as e:
            return BadRequestResponse(message=e.message)

        if extra_days <= 0 and extra_gb <= 0:
            return BadRequestResponse(message="Add at least some days or volume.")

        try:
            proof = PaymentProof.objects.create(
                subscription=subscription,
                kind=PaymentProofKindChoices.RENEWAL,
                amount=price,
                extra_days=extra_days,
                extra_gb=extra_gb,
                receipt_image=data.get("receipt_image"),
                receipt_text=data.get("receipt_text", ""),
            )
        except Exception as e:
            return ServerErrorResponse(errors=str(e))

        try:
            analyze_payment_receipt(proof)
        except Exception:
            pass

        return SuccessResponse(
            data={
                "amount": str(price),
                "extra_days": extra_days,
                "extra_gb": extra_gb,
            },
            message="Renewal submitted. An admin will review your payment shortly.",
        )
