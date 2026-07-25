from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.parsers import JSONParser
from drf_yasg.utils import swagger_auto_schema

from apps.vpn.models import VpnPlan, VpnPricingConfig
from apps.vpn.serializers.vpn_plans import (
    VpnPlanSerializer,
    CustomPlanOptionsSerializer,
    CustomPlanQuoteSerializer,
)
from config.utils.response import SuccessResponse, BadRequestResponse, ServerErrorResponse
from config.utils.custom_serializers import create_response_serializer
from config.utils.exceptions import AppException


class VpnPlanListView(APIView):
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]

    @swagger_auto_schema(
        operation_description="List active fixed VPN plans",
        responses={200: create_response_serializer(
            data_serializer_class=VpnPlanSerializer,
            text_message="Plans retrieved successfully",
        )},
    )
    def get(self, request):
        plans = VpnPlan.objects.filter(is_active=True)
        return SuccessResponse(
            data=VpnPlanSerializer(plans, many=True).data,
            message="Plans retrieved successfully",
        )


class CustomPlanOptionsView(APIView):
    """
    Returns the allowed GB/day/user ranges + unit prices so the client can
    render the custom plan builder (selects/sliders) and preview price live.
    """
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]

    @swagger_auto_schema(
        operation_description="Get allowed ranges and unit prices for building a custom plan",
        responses={200: create_response_serializer(
            data_serializer_class=CustomPlanOptionsSerializer,
            text_message="Custom plan options retrieved successfully",
        )},
    )
    def get(self, request):
        try:
            config = VpnPricingConfig.get_active()
        except ValueError as e:
            return BadRequestResponse(message=str(e))
        return SuccessResponse(
            data=CustomPlanOptionsSerializer(config).data,
            message="Custom plan options retrieved successfully",
        )


class CustomPlanQuoteView(APIView):
    """
    Price preview only - does NOT create a subscription. The client calls
    this every time the user changes GB/days/users in the plan builder.
    """
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(request_body=CustomPlanQuoteSerializer)
    def post(self, request):
        serializer = CustomPlanQuoteSerializer(data=request.data)
        try:
            if not serializer.is_valid():
                return BadRequestResponse(errors=serializer.errors)
            return SuccessResponse(
                data={"price": serializer.validated_data["price"]},
                message="Price calculated successfully",
            )
        except AppException as e:
            return BadRequestResponse(message=e.message)
        except Exception as e:
            return ServerErrorResponse(errors=str(e))
