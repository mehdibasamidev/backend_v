from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from drf_yasg.utils import swagger_auto_schema

from apps.vpn.models import UserVpnSubscription
from apps.vpn.serializers import UserVpnSubscriptionSerializer
from config.utils.custom_serializers import create_response_serializer
from config.utils.pagination import StandardResultsSetPagination


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
        subscriptions = UserVpnSubscription.objects.filter(
            user=request.user
        ).select_related("plan", "payment_proof").order_by("-created_at")

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(subscriptions, request, view=self)
        serializer = UserVpnSubscriptionSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)
