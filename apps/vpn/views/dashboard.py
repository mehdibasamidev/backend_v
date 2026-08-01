from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from drf_yasg.utils import swagger_auto_schema

from apps.vpn.models import UserVpnSubscription
from apps.vpn.serializers.subscriptions import UserVpnSubscriptionSerializer
from apps.vpn.services.lazy_sync import lazy_sync
from config.utils.custom_serializers import create_response_serializer
from config.utils.pagination import StandardResultsSetPagination
from config.utils.response import (
    SuccessResponse,
    BadRequestResponse,
    NotFoundResponse,
)


class UserVpnSubscriptionListView(APIView):
    """
    The user's VPN dashboard - every subscription they own, regardless of
    whether it was bought for themselves or for someone else.
    """
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]
    pagination_class = StandardResultsSetPagination

    @swagger_auto_schema(
        operation_description="List the authenticated user's VPN subscriptions",
        responses={200: create_response_serializer(
            data_serializer_class=UserVpnSubscriptionSerializer,
            text_message="Subscriptions retrieved successfully",
        )},
    )
    def get(self, request):
        # payment_proofs is a reverse FK (a subscription accumulates one
        # proof per purchase/renewal), so it needs prefetch_related -
        # select_related only works for forward FK/OneToOne. The serializer
        # reads it twice per row (latest_payment_proof + has_pending_payment),
        # so without the prefetch this is an N+1.
        subscriptions = (
            UserVpnSubscription.objects
            # hidden_at is the customer's own "clear this from my list"
            # flag - the row still exists for the admin.
            .filter(user=request.user, hidden_at__isnull=True)
            .select_related("plan")
            .prefetch_related("payment_proofs")
            .order_by("-created_at")
        )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(subscriptions, request, view=self)

        # Refresh this page from the panel before serializing, so the user
        # sees live traffic rather than whatever the last cron run stored.
        # Throttled and failure-tolerant - see services/lazy_sync.py.
        lazy_sync(page)

        serializer = UserVpnSubscriptionSerializer(
            page, many=True, context={'request': request}
        )
        return paginator.get_paginated_response(serializer.data)


class HideSubscriptionView(APIView):
    """
    Lets a customer clear a finished service out of their list.

    Soft delete on purpose: the row carries the PaymentProof records that
    prove they paid, so removing it outright would destroy the only
    evidence either side has in a dispute. The admin still sees everything.
    """
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]

    @swagger_auto_schema(
        operation_description=(
            "Hide a finished (expired / rejected / cancelled) subscription "
            "from the caller's own list."
        ),
    )
    def delete(self, request, subscription_id):
        try:
            subscription = UserVpnSubscription.objects.get(
                id=subscription_id, user=request.user,
            )
        except UserVpnSubscription.DoesNotExist:
            return NotFoundResponse(message="Subscription not found")

        if subscription.is_hidden:
            return SuccessResponse(message="Already removed from your list")

        if not subscription.can_be_hidden:
            return BadRequestResponse(
                message=(
                    "Only finished services can be removed. An active "
                    "service, or one with a payment still under review, "
                    "cannot be cleared yet."
                )
            )

        subscription.hidden_at = timezone.now()
        subscription.save(update_fields=["hidden_at", "updated_at"])
        return SuccessResponse(message="Removed from your list")
