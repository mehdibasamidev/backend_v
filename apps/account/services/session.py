from rest_framework_simplejwt.tokens import RefreshToken


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
