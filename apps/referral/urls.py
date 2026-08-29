from django.urls import path

from apps.referral.views.referral import (
    AdminReferralCodeDetailView,
    AdminReferralCodeListCreateView,
    AdminReferralListView,
    AdminReferralStatsView,
    AdminReferralSettingsView,
    MyReferralCodeView,
    ReferralSettingsPublicView,
    ValidateReferralCodeView,
)

urlpatterns = [
    # Public - the signup screen reads this before drawing its form.
    path("referral/settings/", ReferralSettingsPublicView.as_view(), name="referral-settings"),
    path("referral/validate/", ValidateReferralCodeView.as_view(), name="referral-validate"),

    path("referral/me/", MyReferralCodeView.as_view(), name="referral-me"),

    path("referral/admin/settings/", AdminReferralSettingsView.as_view(), name="referral-admin-settings"),
    path("referral/admin/codes/", AdminReferralCodeListCreateView.as_view(), name="referral-admin-codes"),
    path("referral/admin/codes/<uuid:code_id>/", AdminReferralCodeDetailView.as_view(), name="referral-admin-code-detail"),

    # Who came in through whom.
    path("referral/admin/referrals/", AdminReferralListView.as_view(), name="referral-admin-list"),
    path("referral/admin/stats/", AdminReferralStatsView.as_view(), name="referral-admin-stats"),
]
