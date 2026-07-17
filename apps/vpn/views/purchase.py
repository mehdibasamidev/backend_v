from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.parsers import JSONParser
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers

from apps.vpn.models import VpnPlan, UserVpnSubscription, PlanSourceChoices, SubscriptionStatusChoices
from apps.vpn.serializers.vpn_plans import CustomPlanQuoteSerializer
from apps.vpn.serializers.subscriptions import UserVpnSubscriptionSerializer
from config.utils.response import SuccessResponse, BadRequestResponse, ServerErrorResponse
from config.utils.custom_serializers import create_response_serializer
from config.utils.exceptions import AppException


class PurchaseFixedPlanRequestSerializer(serializers.Serializer):
    plan_id = serializers.UUIDField()
    label = serializers.CharField(required=False, allow_blank=True, max_length=100)


class PurchaseFixedPlanView(APIView):
    """
    Buys one of the admin-defined fixed plans. This only creates a
    'pending_payment' subscription - the user still has to upload a payment
    proof (see PaymentProofUploadView) before an admin activates it.
    """
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(
        request_body=PurchaseFixedPlanRequestSerializer,
        responses={201: create_response_serializer(
            data_serializer_class=UserVpnSubscriptionSerializer,
            text_message="Subscription created, awaiting payment",
        )},
    )
    def post(self, request):
        serializer = PurchaseFixedPlanRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        try:
            plan = VpnPlan.objects.get(id=serializer.validated_data["plan_id"], is_active=True)
        except VpnPlan.DoesNotExist:
            return BadRequestResponse(message="Selected plan was not found or is no longer available")

        try:
            subscription = UserVpnSubscription.objects.create(
                user=request.user,
                label=serializer.validated_data.get("label", ""),
                source=PlanSourceChoices.FIXED,
                plan=plan,
                volume_gb=plan.volume_gb,
                duration_days=plan.duration_days,
                max_concurrent_users=plan.max_concurrent_users,
                price=plan.price,
                status=SubscriptionStatusChoices.PENDING_PAYMENT,
            )
            return SuccessResponse(
                data=UserVpnSubscriptionSerializer(subscription).data,
                message="Subscription created. Please submit your payment proof to activate it.",
            )
        except Exception as e:
            return ServerErrorResponse(errors=str(e))


class PurchaseCustomPlanView(APIView):
    """
    Buys a user-defined ("build your own") plan. Price is always
    recalculated server-side from VpnPricingConfig - client-sent prices are
    never trusted.
    """
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(
        request_body=CustomPlanQuoteSerializer,
        responses={201: create_response_serializer(
            data_serializer_class=UserVpnSubscriptionSerializer,
            text_message="Custom subscription created, awaiting payment",
        )},
    )
    def post(self, request):
        serializer = CustomPlanQuoteSerializer(data=request.data)
        try:
            if not serializer.is_valid():
                return BadRequestResponse(errors=serializer.errors)

            data = serializer.validated_data
            subscription = UserVpnSubscription.objects.create(
                user=request.user,
                label=data.get("label", ""),
                source=PlanSourceChoices.CUSTOM,
                plan=None,
                volume_gb=data["volume_gb"],
                duration_days=data["duration_days"],
                max_concurrent_users=data["max_concurrent_users"],
                price=data["price"],
                status=SubscriptionStatusChoices.PENDING_PAYMENT,
            )
            return SuccessResponse(
                data=UserVpnSubscriptionSerializer(subscription).data,
                message="Subscription created. Please submit your payment proof to activate it.",
            )
        except AppException as e:
            return BadRequestResponse(message=e.message)
        except Exception as e:
            return ServerErrorResponse(errors=str(e))
