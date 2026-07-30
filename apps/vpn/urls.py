from django.urls import path

from apps.vpn.views.plans import VpnPlanListView, CustomPlanOptionsView, CustomPlanQuoteView
from apps.vpn.views.checkout import CheckoutView, RenewSubscriptionView
from apps.vpn.views.dashboard import UserVpnSubscriptionListView
from apps.vpn.views.admin_actions import ReviewPaymentProofView
from apps.vpn.views.admin_panel import (
    AdminPlanListCreateView,
    AdminPlanDetailView,
    AdminPricingConfigView,
    AdminPaymentProofListView,
    AdminPendingCountView,
    AdminSubscriptionListView,
    AdminSubscriptionActionView,
)
from apps.vpn.views.payment_info import PaymentInfoView
from apps.vpn.views.receipt import PaymentReceiptView
from apps.vpn.views.subscription_configs import SubscriptionConfigsView

urlpatterns = [
    # ---------- Public / customer ----------
    path("vpn/payment-info/", PaymentInfoView.as_view(), name="vpn-payment-info"),
    path("vpn/plans/", VpnPlanListView.as_view(), name="vpn-plan-list"),
    path("vpn/plans/custom/options/", CustomPlanOptionsView.as_view(), name="vpn-custom-plan-options"),
    path("vpn/plans/custom/quote/", CustomPlanQuoteView.as_view(), name="vpn-custom-plan-quote"),

    # Single atomic step: pick a plan AND submit the receipt. There is
    # deliberately no "create order, pay later" endpoint - that only
    # produced orphan pending records for people who browsed and left.
    path("vpn/checkout/", CheckoutView.as_view(), name="vpn-checkout"),

    path(
        "vpn/subscriptions/<uuid:subscription_id>/renew/",
        RenewSubscriptionView.as_view(),
        name="vpn-subscription-renew",
    ),
    path(
        "vpn/subscriptions/<uuid:subscription_id>/configs/",
        SubscriptionConfigsView.as_view(),
        name="vpn-subscription-configs",
    ),
    path("vpn/subscriptions/", UserVpnSubscriptionListView.as_view(), name="vpn-subscription-list"),

    # Streams the receipt after checking the caller owns it or is staff.
    # The storage bucket itself stays private.
    path(
        "vpn/payment-proofs/<uuid:payment_proof_id>/receipt/",
        PaymentReceiptView.as_view(),
        name="vpn-payment-receipt",
    ),

    # ---------- Admin ----------
    path("vpn/admin/counts/", AdminPendingCountView.as_view(), name="vpn-admin-counts"),

    path("vpn/admin/plans/", AdminPlanListCreateView.as_view(), name="vpn-admin-plan-list"),
    path("vpn/admin/plans/<uuid:plan_id>/", AdminPlanDetailView.as_view(), name="vpn-admin-plan-detail"),

    path("vpn/admin/pricing-config/", AdminPricingConfigView.as_view(), name="vpn-admin-pricing-config"),

    path("vpn/admin/payment-proofs/", AdminPaymentProofListView.as_view(), name="vpn-admin-payment-proof-list"),
    path(
        "vpn/admin/payment-proofs/<uuid:payment_proof_id>/review/",
        ReviewPaymentProofView.as_view(),
        name="vpn-review-payment-proof",
    ),

    path("vpn/admin/subscriptions/", AdminSubscriptionListView.as_view(), name="vpn-admin-subscription-list"),
    path(
        "vpn/admin/subscriptions/<uuid:subscription_id>/<str:action>/",
        AdminSubscriptionActionView.as_view(),
        name="vpn-admin-subscription-action",
    ),
]
