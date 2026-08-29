from django.urls import path

from apps.account.views.auth import (
    AddEmailStartView,
    AddEmailVerifyView,
    AddPhoneStartView,
    AddPhoneVerifyView,
    AuthSettingsPublicView,
    PasswordLoginView,
    ResetPasswordConfirmView,
    ResetPasswordStartView,
    SetUsernameView,
)
from apps.account.views.google import GoogleSignInView
from apps.account.views.profile import UserProfileView
from apps.account.views.user_search import UserSearchView
from apps.account.views.register import (
    EmailRegisterView,
    EmailVerifyView,
    PhoneRegisterStartView,
    PhoneVerifyView,
)

urlpatterns = [
    # Tells the client whether email signups need a code.
    path("auth/settings/", AuthSettingsPublicView.as_view(), name="auth-settings"),

    # Phone: one pair of endpoints covers both signup and sign-in - the
    # server already knows whether the number exists, so the client doesn't
    # need an account-existence probe.
    path("auth/phone/start/", PhoneRegisterStartView.as_view(), name="auth-phone-start"),
    path("auth/phone/verify/", PhoneVerifyView.as_view(), name="auth-phone-verify"),

    # Sign in or sign up in one call - the address decides which.
    path("auth/email/register/", EmailRegisterView.as_view(), name="auth-email-register"),
    path("auth/email/verify/", EmailVerifyView.as_view(), name="auth-email-verify"),

    # Email, phone or username - all three in `identifier`.
    path("auth/login/", PasswordLoginView.as_view(), name="auth-login"),

    path("auth/username/", SetUsernameView.as_view(), name="auth-set-username"),

    path("auth/email/add/", AddEmailStartView.as_view(), name="auth-add-email"),
    path("auth/email/add/verify/", AddEmailVerifyView.as_view(), name="auth-add-email-verify"),
    path("auth/phone/add/", AddPhoneStartView.as_view(), name="auth-add-phone"),
    path("auth/phone/add/verify/", AddPhoneVerifyView.as_view(), name="auth-add-phone-verify"),

    path("auth/password/reset/", ResetPasswordStartView.as_view(), name="auth-reset-password"),
    path("auth/password/reset/confirm/", ResetPasswordConfirmView.as_view(), name="auth-reset-password-confirm"),

    path("auth/google-signin/", GoogleSignInView.as_view(), name="google-signin"),

    # ---------- Profile ----------
    path("profile/", UserProfileView.as_view(), name="user-profile"),
    path("users/search/", UserSearchView.as_view(), name="user-search"),
]

# Removed since the multi-identifier rollout:
#   auth/check-email/    - an account-existence oracle; the register call
#                          now rejects a duplicate address directly.
#   auth/register/       - replaced by auth/email/register/
#   auth/register-vpn/   - replaced by auth/phone/start + verify
#   auth/verify-otp/     - replaced by the per-purpose verify endpoints
