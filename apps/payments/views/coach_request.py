# apps/payments/views/coach_service.py

from rest_framework.permissions import IsAuthenticated
from drf_yasg.utils import swagger_auto_schema
from rest_framework.views import APIView

from apps.payments.models.coach_service import CoachService
from apps.payments.serializers.coach_service import CoachServiceRequestSerializer, CoachServiceSerializer
from apps.payments.services.coach_request_service import CoachRequestService
from apps.payments.services.transaction_service import PaymentTransactionService
from apps.payments.services.stripe_service import StripeService
from config.utils.response import SuccessResponse, SuccessResponse201


class CoachServiceRequestView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Create a coach service request and payment transaction",
        request_body=CoachServiceRequestSerializer,
        responses={201: CoachServiceRequestSerializer}
    )
    def post(self, request):
        serializer = CoachServiceRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        request_obj = CoachRequestService.create_request(request.user, serializer.validated_data)

        # Create PaymentTransaction for the coach service
        transaction = PaymentTransactionService.create_transaction(
            user=request.user,
            amount=request_obj.price,
            payment_type='coach_service',
            metadata={'coach_id': str(request_obj.coach.id), 'request_id': str(request_obj.id), 'title': request_obj.title}
        )

        # Stripe Checkout Session
        checkout_session = StripeService.create_checkout_session(
            amount=request_obj.price,
            success_url=f"{request.build_absolute_uri('/payments/success/?transaction_id=')}{transaction.id}",
            cancel_url=f"{request.build_absolute_uri('/payments/cancel/')}?transaction_id={transaction.id}",
            metadata={'transaction_id': str(transaction.id), 'coach_request_id': str(request_obj.id)}
        )
        return SuccessResponse201(data={'checkout_url': checkout_session.url, 'request_id': request_obj.id},)


# ----- Coach Services -----
class CoachServiceListView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="List all active coach services",
        responses={200: CoachServiceSerializer(many=True)}
    )
    def get(self, request):
        services = CoachService.objects.filter(is_active=True)
        serializer = CoachServiceSerializer(services, many=True)
        return SuccessResponse(data=serializer.data)
