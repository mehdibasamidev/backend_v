from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer

from apps.vpn.models import UserVpnSubscription, SubscriptionStatusChoices
from apps.vpn.services.provisioning import get_client_configs
from config.utils.response import SuccessResponse, BadRequestResponse, ServerErrorResponse


class SubscriptionConfigsView(APIView):
    """
    Individual per-location config links (vless://, vmess://, ...) for one
    of the user's own active subscriptions - shown alongside the single
    subscription link so the user can pick/copy a specific server config.
    """
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]

    def get(self, request, subscription_id):
        try:
            subscription = UserVpnSubscription.objects.get(
                id=subscription_id, user=request.user,
            )
        except UserVpnSubscription.DoesNotExist:
            return BadRequestResponse(message="Subscription not found")

        if subscription.status != SubscriptionStatusChoices.ACTIVE:
            return BadRequestResponse(message="This subscription is not active yet")

        try:
            configs = get_client_configs(subscription)
            return SuccessResponse(
                data={"configs": configs},
                message="Configs retrieved successfully",
            )
        except Exception as e:
            return ServerErrorResponse(errors=str(e))
