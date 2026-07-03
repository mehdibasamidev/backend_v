# apps/payments/services/stripe_service.py
import stripe
from django.conf import settings

stripe.api_key = settings.STRIPE_SECRET_KEY


class StripeService:
    @staticmethod
    def construct_event(payload, sig_header):
        """
        Verify the Stripe webhook signature and return the event.
        """
        from django.conf import settings

        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
            )
            return event
        except ValueError:
            raise ValueError("Invalid payload")
        except stripe.error.SignatureVerificationError:
            raise ValueError("Invalid signature")

    @staticmethod
    def create_checkout_session(amount, success_url, cancel_url, metadata, customer_email=None):
        """
        Create a Stripe Checkout session for payment.
        """
        return stripe.checkout.Session.create(
            payment_method_types=['card'],
            mode='payment',
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {'name': metadata.get('title', 'Fitness Service')},
                    'unit_amount': int(amount * 100),
                },
                'quantity': 1,
            }],
            customer_email=customer_email,
            success_url=success_url,
            cancel_url=cancel_url,
            metadata=metadata
        )
