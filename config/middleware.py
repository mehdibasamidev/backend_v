from urllib.parse import parse_qs
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth import get_user_model
from channels.db import database_sync_to_async
from rest_framework_simplejwt.tokens import AccessToken

User = get_user_model()


@database_sync_to_async
def get_user_from_token(token):
    try:
        access_token = AccessToken(token)
        print(f"Decoded token: {access_token}")
        user_id = access_token["user_id"]
        return User.objects.get(id=user_id)

    except Exception as e:
        print(f"Token auth error: {e}")
        return AnonymousUser()


class TokenAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        query_string = scope.get("query_string", b"").decode()
        params = parse_qs(query_string)

        token = params.get("token", [None])[0]

        scope["user"] = await get_user_from_token(token)
        print(f"Authenticated user: {scope['user']} with token: {token}")

        return await self.app(scope, receive, send)
