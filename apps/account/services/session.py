from rest_framework_simplejwt.tokens import RefreshToken

from apps.account.serializers.profile import UserInfoSerializer


def issue_session(user):
    """
    The token pair every successful auth path returns.

    Kept in one place so a new sign-in route can't accidentally ship a
    different response shape than the client expects.
    """
    refresh = RefreshToken.for_user(user)
    return {
        "access_token": str(refresh.access_token),
        "refresh_token": str(refresh),
    }


def auth_payload(user):
    """
    Tokens plus the user, the body a sign-in returns. A service rather than
    a view helper because "Login with Telegram" (apps/bot) hands out the
    same session and must not grow its own copy of the shape.
    """
    return {**issue_session(user), "user": UserInfoSerializer(user).data}
