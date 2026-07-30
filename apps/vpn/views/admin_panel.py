from django.db.models import Q
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAdminUser
from rest_framework.renderers import JSONRenderer
from rest_framework.parsers import JSONParser
from rest_framework.views import APIView

from apps.vpn.models import (
    VpnPlan,
    VpnPricingConfig,
    UserVpnSubscription,
    PaymentProof,
)
from apps.vpn.serializers.admin import (
    AdminVpnPlanSerializer,
    AdminPricingConfigSerializer,
    AdminPaymentProofSerializer,
    AdminSubscriptionSerializer,
)
from apps.vpn.services.provisioning import sync_subscription_usage
from apps.vpn.services.xui_client import ThreeXUiClient
from config.utils.custom_serializers import create_response_serializer
from config.utils.pagination import StandardResultsSetPagination
from config.utils.response import (
    SuccessResponse,
    SuccessResponse201,
    BadRequestResponse,
    NotFoundResponse,
)


# ==========================================================
# Plans
# ==========================================================

class AdminPlanListCreateView(APIView):
    permission_classes = [IsAdminUser]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]
    pagination_class = StandardResultsSetPagination

    @swagger_auto_schema(
        operation_description="List every plan, including inactive ones",
        responses={200: create_response_serializer(
            data_serializer_class=AdminVpnPlanSerializer,
            text_message="Plans retrieved successfully",
        )},
    )
    def get(self, request):
        plans = VpnPlan.objects.all()
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(plans, request, view=self)
        return paginator.get_paginated_response(
            AdminVpnPlanSerializer(page, many=True).data
        )

    @swagger_auto_schema(request_body=AdminVpnPlanSerializer)
    def post(self, request):
        serializer = AdminVpnPlanSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)
        serializer.save()
        return SuccessResponse201(
            data=serializer.data, message="Plan created successfully"
        )


class AdminPlanDetailView(APIView):
    permission_classes = [IsAdminUser]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    def _get_plan(self, plan_id):
        return VpnPlan.objects.filter(id=plan_id).first()

    @swagger_auto_schema(
        responses={200: create_response_serializer(
            data_serializer_class=AdminVpnPlanSerializer,
            text_message="Plan retrieved successfully",
        )},
    )
    def get(self, request, plan_id):
        plan = self._get_plan(plan_id)
        if not plan:
            return NotFoundResponse(message="Plan not found")
        return SuccessResponse(
            data=AdminVpnPlanSerializer(plan).data,
            message="Plan retrieved successfully",
        )

    @swagger_auto_schema(request_body=AdminVpnPlanSerializer)
    def patch(self, request, plan_id):
        plan = self._get_plan(plan_id)
        if not plan:
            return NotFoundResponse(message="Plan not found")

        serializer = AdminVpnPlanSerializer(plan, data=request.data, partial=True)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)
        serializer.save()
        return SuccessResponse(
            data=serializer.data, message="Plan updated successfully"
        )

    def delete(self, request, plan_id):
        plan = self._get_plan(plan_id)
        if not plan:
            return NotFoundResponse(message="Plan not found")

        # Subscriptions snapshot their own volume/duration/price at purchase
        # time and reference the plan with on_delete=SET_NULL, so deleting a
        # plan never corrupts an existing order. Deactivating is still
        # usually the better move - it keeps the name readable in history.
        if plan.subscriptions.exists():
            plan.is_active = False
            plan.save(update_fields=["is_active", "updated_at"])
            return SuccessResponse(
                message="Plan has existing orders, so it was deactivated instead of deleted",
                data=AdminVpnPlanSerializer(plan).data,
            )

        plan.delete()
        return SuccessResponse(message="Plan deleted successfully")


# ==========================================================
# Pricing config (custom plan builder)
# ==========================================================

class AdminPricingConfigView(APIView):
    """
    Reads/updates the single active VpnPricingConfig. Kept as one resource
    rather than a list because the rest of the app only ever asks for the
    active row (VpnPricingConfig.get_active).
    """
    permission_classes = [IsAdminUser]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(
        responses={200: create_response_serializer(
            data_serializer_class=AdminPricingConfigSerializer,
            text_message="Pricing config retrieved successfully",
        )},
    )
    def get(self, request):
        config = VpnPricingConfig.objects.filter(is_active=True).first()
        if not config:
            return NotFoundResponse(
                message="No active pricing config exists yet. Create one first."
            )
        return SuccessResponse(
            data=AdminPricingConfigSerializer(config).data,
            message="Pricing config retrieved successfully",
        )

    @swagger_auto_schema(request_body=AdminPricingConfigSerializer)
    def post(self, request):
        serializer = AdminPricingConfigSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        # Only one row may be active - get_active() takes the first match,
        # so leaving two active would make pricing non-deterministic.
        if serializer.validated_data.get("is_active", True):
            VpnPricingConfig.objects.filter(is_active=True).update(is_active=False)

        serializer.save()
        return SuccessResponse201(
            data=serializer.data, message="Pricing config created successfully"
        )

    @swagger_auto_schema(request_body=AdminPricingConfigSerializer)
    def patch(self, request):
        config = VpnPricingConfig.objects.filter(is_active=True).first()
        if not config:
            return NotFoundResponse(
                message="No active pricing config exists yet. Create one first."
            )

        serializer = AdminPricingConfigSerializer(config, data=request.data, partial=True)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)
        serializer.save()
        return SuccessResponse(
            data=serializer.data,
            message="Pricing config updated successfully",
        )


# ==========================================================
# Payment proof review queue
# ==========================================================

class AdminPaymentProofListView(APIView):
    """
    The review queue. Defaults to pending only, since that's what an admin
    opens the screen to act on.
    """
    permission_classes = [IsAdminUser]
    renderer_classes = [JSONRenderer]
    pagination_class = StandardResultsSetPagination

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                "status", openapi.IN_QUERY,
                description="pending (default) | approved | rejected | all",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "kind", openapi.IN_QUERY,
                description="purchase | renewal",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "q", openapi.IN_QUERY,
                description="Search by buyer email or full name",
                type=openapi.TYPE_STRING,
            ),
        ],
        responses={200: create_response_serializer(
            data_serializer_class=AdminPaymentProofSerializer,
            text_message="Payment proofs retrieved successfully",
        )},
    )
    def get(self, request):
        proofs = PaymentProof.objects.select_related(
            "subscription", "subscription__user", "subscription__plan"
        )

        status_filter = request.GET.get("status", "pending")
        if status_filter == "pending":
            proofs = proofs.filter(is_approved__isnull=True)
        elif status_filter == "approved":
            proofs = proofs.filter(is_approved=True)
        elif status_filter == "rejected":
            proofs = proofs.filter(is_approved=False)

        kind = request.GET.get("kind")
        if kind:
            proofs = proofs.filter(kind=kind)

        query = request.GET.get("q", "").strip()
        if query:
            proofs = proofs.filter(
                Q(subscription__user__email__icontains=query)
                | Q(subscription__user__full_name__icontains=query)
            )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(proofs, request, view=self)
        return paginator.get_paginated_response(
            AdminPaymentProofSerializer(
                page, many=True, context={'request': request}
            ).data
        )


class AdminPendingCountView(APIView):
    """Badge count for the admin nav - cheap enough to poll."""
    permission_classes = [IsAdminUser]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        return SuccessResponse(
            data={
                "pending_proofs": PaymentProof.objects.filter(
                    is_approved__isnull=True
                ).count(),
            },
            message="Counts retrieved successfully",
        )


# ==========================================================
# Subscriptions
# ==========================================================

class AdminSubscriptionListView(APIView):
    permission_classes = [IsAdminUser]
    renderer_classes = [JSONRenderer]
    pagination_class = StandardResultsSetPagination

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                "status", openapi.IN_QUERY,
                description="active | pending_approval | expired | rejected | cancelled",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "q", openapi.IN_QUERY,
                description="Search by user email, full name, or panel client email",
                type=openapi.TYPE_STRING,
            ),
        ],
        responses={200: create_response_serializer(
            data_serializer_class=AdminSubscriptionSerializer,
            text_message="Subscriptions retrieved successfully",
        )},
    )
    def get(self, request):
        subscriptions = UserVpnSubscription.objects.select_related("user", "plan")

        status_filter = request.GET.get("status")
        if status_filter:
            subscriptions = subscriptions.filter(status=status_filter)

        query = request.GET.get("q", "").strip()
        if query:
            subscriptions = subscriptions.filter(
                Q(user__email__icontains=query)
                | Q(user__full_name__icontains=query)
                | Q(xui_client_email__icontains=query)
                | Q(label__icontains=query)
            )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(subscriptions, request, view=self)
        return paginator.get_paginated_response(
            AdminSubscriptionSerializer(page, many=True).data
        )


class AdminSubscriptionActionView(APIView):
    """
    Panel-side actions on one subscription: pull fresh usage, or
    disable/enable the client on 3x-ui without deleting it.
    """
    permission_classes = [IsAdminUser]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    ALLOWED_ACTIONS = ("sync", "disable", "enable")

    @swagger_auto_schema(
        operation_description="action = sync | disable | enable",
    )
    def post(self, request, subscription_id, action):
        if action not in self.ALLOWED_ACTIONS:
            return BadRequestResponse(
                message=f"Unknown action. Allowed: {', '.join(self.ALLOWED_ACTIONS)}"
            )

        subscription = UserVpnSubscription.objects.filter(id=subscription_id).first()
        if not subscription:
            return NotFoundResponse(message="Subscription not found")

        if not subscription.xui_client_email:
            return BadRequestResponse(
                message="This subscription has no provisioned panel client yet"
            )

        if action == "sync":
            subscription = sync_subscription_usage(subscription)
        else:
            client = ThreeXUiClient()
            emails = [subscription.xui_client_email]
            if action == "disable":
                client.bulk_disable(emails)
            else:
                client.bulk_enable(emails)

        return SuccessResponse(
            data=AdminSubscriptionSerializer(subscription).data,
            message=f"Action '{action}' completed successfully",
        )
