from django.conf import settings
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer

from config.utils.response import SuccessResponse


class PaymentInfoView(APIView):
    """
    Static card-to-card payment details for the payment instructions screen.
    Kept as a tiny endpoint (rather than hardcoded in the client) so the
    card can be changed without an app store release.
    """
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        return SuccessResponse(
            data={
                "card_number": settings.PAYMENT_CARD_NUMBER,
                "card_holder": settings.PAYMENT_CARD_HOLDER,
            },
            message="Payment info retrieved successfully",
        )
