# apps/payments/views/subscription.py

from rest_framework.views import APIView
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated, AllowAny

from apps.payments.models import SubscriptionPlan
from apps.payments.serializers.subscription import SubscriptionPlanSerializer
from config.utils.response import SuccessResponse
from apps.payments.services.transaction_service import PaymentTransactionService
from apps.payments.services.stripe_service import StripeService


class SubscriptionPlanListView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary="List subscription plans",
        responses={200: SubscriptionPlanSerializer(many=True)}
    )
    def get(self, request):
        plans = SubscriptionPlan.objects.filter(is_active=True)
        return SuccessResponse(
            SubscriptionPlanSerializer(plans, many=True).data
        )


class SubscribePlanView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Subscribe to a plan (creates Stripe checkout session)",
        request_body=None,
        responses={200: 'Stripe checkout session URL'}
    )
    def post(self, request, plan_id):
        plan = SubscriptionPlan.objects.get(id=plan_id)
        # 1️⃣ Create PaymentTransaction
        transaction = PaymentTransactionService.create_payment(
            user=request.user,
            amount=plan.price,
            payment_type='subscription',
            metadata={'plan_id': str(plan.id), 'title': plan.title}
        )
        # 2️⃣ Create Stripe Checkout Session
        checkout_session = StripeService.create_checkout_session(
            amount=plan.price,
            success_url=f"{request.build_absolute_uri('/payments/success/?transaction_id=')}{transaction.id}",
            cancel_url=f"{request.build_absolute_uri('/payments/cancel/')}?transaction_id={transaction.id}",
            metadata={'transaction_id': str(transaction.id), 'plan_id': str(plan.id)}
        )
        return SuccessResponse(data={'checkout_url': checkout_session.url})
