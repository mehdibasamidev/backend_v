from rest_framework.views import APIView
from rest_framework.permissions import IsAdminUser
from rest_framework.renderers import JSONRenderer
from rest_framework.parsers import JSONParser
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers

from apps.vpn.models import PaymentProof
from apps.vpn.services.provisioning import activate_subscription, reject_subscription
from config.utils.response import SuccessResponse, BadRequestResponse, ServerErrorResponse


class ReviewPaymentRequestSerializer(serializers.Serializer):
    approve = serializers.BooleanField()
    admin_note = serializers.CharField(required=False, allow_blank=True)


class ReviewPaymentProofView(APIView):
    """
    Lets a staff user approve/reject a payment proof from an API client
    (e.g. an admin mobile app), as an alternative to the Django admin actions.
    """
    permission_classes = [IsAdminUser]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(request_body=ReviewPaymentRequestSerializer)
    def post(self, request, payment_proof_id):
        try:
            proof = PaymentProof.objects.select_related("subscription").get(id=payment_proof_id)
        except PaymentProof.DoesNotExist:
            return BadRequestResponse(message="Payment proof not found")

        if proof.is_approved is not None:
            return BadRequestResponse(message="This payment has already been reviewed")

        serializer = ReviewPaymentRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        try:
            proof.is_approved = serializer.validated_data["approve"]
            proof.admin_note = serializer.validated_data.get("admin_note", "")
            proof.reviewed_by = request.user
            proof.reviewed_at = timezone.now()
            proof.save(update_fields=["is_approved", "admin_note", "reviewed_by", "reviewed_at"])

            if proof.is_approved:
                activate_subscription(proof.subscription)
                message = "Payment approved and VPN service activated"
            else:
                reject_subscription(proof.subscription)
                message = "Payment rejected"

            return SuccessResponse(message=message, data={"subscription_status": proof.subscription.status})
        except Exception as e:
            return ServerErrorResponse(errors=str(e))
