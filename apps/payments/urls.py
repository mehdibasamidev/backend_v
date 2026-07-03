# apps/payments/urls.py

from django.urls import path
from apps.payments.views.subscription import (
    SubscriptionPlanListView,
    SubscribePlanView,
)
from apps.payments.views.coach_request import (
    CoachServiceRequestView,
    CoachServiceListView,
)
from apps.payments.views.payment_transaction import (
    PaymentTransactionListView,
    # StripeWebhookView,
)

urlpatterns = [

    # -------------------------------
    # Subscription Plans
    # -------------------------------
    path(
        'plans/',
        SubscriptionPlanListView.as_view(),
        name='subscription-plan-list'
    ),

    path(
        'plans/<uuid:plan_id>/subscribe/',
        SubscribePlanView.as_view(),
        name='subscribe-plan'
    ),

    # -------------------------------
    # Coach Services
    # -------------------------------
    # Coach services
    path('coach-services/', CoachServiceListView.as_view(), name='coach-service-list'),

    # Coach service requests
    path('coach-requests/', CoachServiceRequestView.as_view(), name='coach-service-request'),

    # -------------------------------
    # Payments
    # -------------------------------
    # User payments
    path('transactions/', PaymentTransactionListView.as_view(), name='payment-transactions'),

    # path(
    #     'stripe/webhook/',
    #     StripeWebhookView.as_view(),
    #     name='stripe-webhook'
    # ),
]
