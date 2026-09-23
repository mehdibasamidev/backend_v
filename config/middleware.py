from urllib.parse import parse_qs
from django.contrib.auth.models import AnonymousUser
from channels.db import database_sync_to_async
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed


@database_sync_to_async
def get_user_from_token(token):
    """
    The same checks as the REST API, through SimpleJWT's own authenticator:
    a valid access token for a user that exists and is active. Anything
    else is anonymous, which ChatConsumer closes.

    is_active matters here: a bot-only account merged by "Connect Telegram"
    is deactivated but may still hold a 30-day access token, and REST
    already refuses it.
    """
    if not token:
        return AnonymousUser()
    auth = JWTAuthentication()
    try:
        return auth.get_user(auth.get_validated_token(token))
    except AuthenticationFailed:
        # InvalidToken is a subclass: bad or expired token, no user id in
        # it, unknown user, inactive user.
        return AnonymousUser()


class TokenAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        query_string = scope.get("query_string", b"").decode()
        params = parse_qs(query_string)

        token = params.get("token", [None])[0]

        # The token is a live credential - never print or log it.
        scope["user"] = await get_user_from_token(token)

        return await self.app(scope, receive, send)
