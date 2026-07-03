# apps/payments/views/stripe_webhook_views.py
import logging
from django.db import transaction as db_transaction
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from apps.payments.services.stripe_service import StripeService
from apps.payments.services.payment_transaction_service import PaymentTransactionService
from apps.payments.services.coach_request_service import CoachRequestService
from apps.payments.services.subscription_service import SubscriptionService

logger = logging.getLogger("payments.webhook")


@method_decorator(csrf_exempt, name='dispatch')
class StripeWebhookView(APIView):
    """
    Stripe Webhook to handle payment events.
    """

    def post(self, request):
        payload = request.body
        sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')

        try:
            event = StripeService.construct_event(payload, sig_header)
        except ValueError as e:
            logger.error(f"Invalid payload: {e}")
            return Response(status=status.HTTP_400_BAD_REQUEST)
        except StripeService.SignatureVerificationError as e:
            logger.error(f"Stripe signature verification failed: {e}")
            return Response(status=status.HTTP_400_BAD_REQUEST)

        event_type = event['type']
        logger.info(f"Stripe webhook received: {event_type}")

        if event_type == 'checkout.session.completed':
            session = event['data']['object']
            metadata = session.get('metadata', {})

            transaction_id = metadata.get('transaction_id')
            if not transaction_id:
                logger.error("transaction_id missing in webhook metadata")
                return Response(status=status.HTTP_400_BAD_REQUEST)

            try:
                with db_transaction.atomic():
                    # Mark transaction as paid (idempotent inside service)
                    transaction = PaymentTransactionService.mark_paid(transaction_id)

                    # Handle subscription payments
                    if transaction.payment_type == 'subscription':
                        SubscriptionService.activate_or_extend(transaction)

                    # Handle coach service payments
                    elif transaction.payment_type == 'coach_service':
                        coach_request_id = metadata.get('coach_request_id')
                        if coach_request_id:
                            CoachRequestService.mark_paid(
                                request_id=coach_request_id,
                                payment=transaction
                            )

            except Exception as e:
                logger.exception(f"Failed to process webhook for transaction {transaction_id}: {e}")
                return Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(status=status.HTTP_200_OK)
