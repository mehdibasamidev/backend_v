from django.urls import path
from apps.account.views import RegisterView, LoginView, GoogleSignInView, UserProfileView
from apps.account.views.auth import CheckEmailView, VerifyOtpView, RegisterVpnView
from apps.account.views.user_search import UserSearchView


urlpatterns = [
    # Auth
    path('auth/check-email/', CheckEmailView.as_view(), name='check-email'),
    path('auth/register/', RegisterView.as_view(), name='register'),
    path('auth/register-vpn/', RegisterVpnView.as_view(), name='register-vpn'),
    path('auth/verify-otp/', VerifyOtpView.as_view(), name='verify-otp'),
    path('auth/login/', LoginView.as_view(), name='login'),
    path('auth/google-signin/', GoogleSignInView.as_view(), name='google-signin'),

    # Profile
    path('profile/', UserProfileView.as_view(), name='user-profile'),
    # Coach
    path('users/search/', UserSearchView.as_view(), name='user-search'),
]

# Key Architectural Change:
# Old Way: RegisterView → User created → Login.

# New Way: CheckEmailView (Flutter Step 1) → RegisterView (Flutter Step 2: Cache & Send OTP) → VerifyOtpView (Flutter Step 3: Verify & Create User).
