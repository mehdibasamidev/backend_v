from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.parsers import MultiPartParser, JSONParser
from drf_yasg.utils import swagger_auto_schema

from apps.vpn.models import UserVpnSubscription, SubscriptionStatusChoices
from apps.vpn.serializers import PaymentProofSubmitSerializer
from apps.vpn.services.ai_receipt import analyze_payment_receipt
from config.utils.response import SuccessResponse, BadRequestResponse, ServerErrorResponse


class PaymentProofUploadView(APIView):
    """
    User uploads a receipt (image and/or free text) for a pending
    subscription. This never auto-approves the payment - it just queues it
    for admin review, optionally enriched with a best-effort AI opinion.
    """
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]
    parser_classes = [MultiPartParser, JSONParser]

    @swagger_auto_schema(request_body=PaymentProofSubmitSerializer)
    def post(self, request, subscription_id):
        try:
            subscription = UserVpnSubscription.objects.get(id=subscription_id, user=request.user)
        except UserVpnSubscription.DoesNotExist:
            return BadRequestResponse(message="Subscription not found")

        if subscription.status != SubscriptionStatusChoices.PENDING_PAYMENT:
            return BadRequestResponse(message="This subscription is not awaiting payment")

        if hasattr(subscription, "payment_proof"):
            return BadRequestResponse(message="A payment proof was already submitted for this subscription")

        serializer = PaymentProofSubmitSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        try:
            proof = serializer.save(subscription=subscription)
            subscription.status = SubscriptionStatusChoices.PENDING_APPROVAL
            subscription.save(update_fields=["status", "updated_at"])

            try:
                analyze_payment_receipt(proof)
            except Exception:
                # AI review is best-effort only; never block submission on it.
                pass

            return SuccessResponse(
                message="Payment proof submitted. An admin will review it shortly.",
                data={"subscription_id": str(subscription.id), "status": subscription.status},
            )
        except Exception as e:
            return ServerErrorResponse(errors=str(e))
