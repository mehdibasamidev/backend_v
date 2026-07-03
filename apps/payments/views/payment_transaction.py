from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from drf_yasg.utils import swagger_auto_schema
from config.utils.response import SuccessResponse

from apps.payments.serializers.payment_transaction import PaymentTransactionSerializer
from apps.payments.services.transaction_service import PaymentTransactionService
from apps.payments.models.payment_transaction import PaymentTransaction


class PaymentTransactionListView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: PaymentTransactionSerializer(many=True)})
    def get(self, request):
        payments = PaymentTransaction.objects.filter(user=request.user)
        serializer = PaymentTransactionSerializer(payments, many=True)
        return SuccessResponse(serializer.data)


class CreatePaymentCheckoutView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=PaymentTransactionSerializer,
        responses={201: PaymentTransactionSerializer}
    )
    def post(self, request):
        serializer = PaymentTransactionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        payment = PaymentTransactionService.create_payment(
            user=request.user,
            amount=serializer.validated_data['amount'],
            payment_type=serializer.validated_data['payment_type'],
            metadata=serializer.validated_data.get('metadata', {})
        )

        session = PaymentTransactionService.create_checkout_session(
            payment=payment,
            success_url="https://yourapp.com/payment/success",
            cancel_url="https://yourapp.com/payment/cancel"
        )

        return SuccessResponse({
            "payment": PaymentTransactionSerializer(payment).data,
            "checkout_url": session.url
        }, status=201)
