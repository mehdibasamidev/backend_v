from django.urls import path

from apps.vpn.views.plans import VpnPlanListView, CustomPlanOptionsView, CustomPlanQuoteView
from apps.vpn.views.purchase import PurchaseFixedPlanView, PurchaseCustomPlanView
from apps.vpn.views.payments import PaymentProofUploadView
from apps.vpn.views.dashboard import UserVpnSubscriptionListView
from apps.vpn.views.admin_actions import ReviewPaymentProofView
from apps.vpn.views.payment_info import PaymentInfoView
from apps.vpn.views.subscription_configs import SubscriptionConfigsView

urlpatterns = [
    path("vpn/payment-info/", PaymentInfoView.as_view(), name="vpn-payment-info"),
    path("vpn/plans/", VpnPlanListView.as_view(), name="vpn-plan-list"),
    path("vpn/plans/custom/options/", CustomPlanOptionsView.as_view(), name="vpn-custom-plan-options"),
    path("vpn/plans/custom/quote/", CustomPlanQuoteView.as_view(), name="vpn-custom-plan-quote"),

    path("vpn/purchase/fixed/", PurchaseFixedPlanView.as_view(), name="vpn-purchase-fixed"),
    path("vpn/purchase/custom/", PurchaseCustomPlanView.as_view(), name="vpn-purchase-custom"),

    path(
        "vpn/subscriptions/<uuid:subscription_id>/payment-proof/",
        PaymentProofUploadView.as_view(),
        name="vpn-payment-proof-upload",
    ),
    path(
        "vpn/subscriptions/<uuid:subscription_id>/configs/",
        SubscriptionConfigsView.as_view(),
        name="vpn-subscription-configs",
    ),
    path("vpn/subscriptions/", UserVpnSubscriptionListView.as_view(), name="vpn-subscription-list"),

    path(
        "vpn/admin/payment-proofs/<uuid:payment_proof_id>/review/",
        ReviewPaymentProofView.as_view(),
        name="vpn-review-payment-proof",
    ),
]