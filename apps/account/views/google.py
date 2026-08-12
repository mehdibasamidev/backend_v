from django.contrib.auth import get_user_model
from django.db import transaction
from drf_yasg.utils import swagger_auto_schema
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from apps.account.serializers.auth import GoogleSignInSerializer
from apps.account.serializers.profile import UserInfoSerializer
from apps.account.services.session import issue_session
from config.utils.response import (
    SuccessResponse,
    BadRequestResponse,
    ServerErrorResponse,
)

User = get_user_model()


class GoogleSignInView(APIView):
    """
    Signs in (or registers) with a Google ID token.

    Google has already verified the address, so the account is created with
    is_email_verified=True - sending our own code to an inbox Google just
    confirmed would be busywork, and it would strand the user behind a
    verification screen they can't clear.

    No password is set: there is nothing for the user to have chosen. They
    can add one later via the reset flow if they want an email login too.
    """
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(request_body=GoogleSignInSerializer)
    def post(self, request):
        serializer = GoogleSignInSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        from django.conf import settings

        try:
            claims = id_token.verify_oauth2_token(
                serializer.validated_data["access_token"],
                google_requests.Request(),
                getattr(settings, "GOOGLE_CLIENT_ID", None),
            )
        except ValueError:
            return BadRequestResponse(message="Invalid Google token.")
        except Exception as e:
            return ServerErrorResponse(errors=str(e))

        google_id = claims.get("sub")
        email = (claims.get("email") or "").strip().lower()
        if not google_id:
            return BadRequestResponse(message="Google token is missing an account id.")

        try:
            user = self._resolve_user(google_id, email, claims)
        except Exception as e:
            return ServerErrorResponse(errors=str(e))

        return SuccessResponse(
            data={**issue_session(user), "user": UserInfoSerializer(user).data},
            message="Signed in successfully.",
        )

    @transaction.atomic
    def _resolve_user(self, google_id, email, claims):
        user = User.objects.filter(google_id=google_id).first()
        if user:
            return user

        # Someone who signed up with this address by email first: link the
        # two rather than failing on the unique constraint or creating a
        # duplicate account they can never reconcile.
        if email:
            user = User.objects.filter(email__iexact=email).first()
            if user:
                user.google_id = google_id
                user.is_email_verified = True
                user.save(update_fields=["google_id", "is_email_verified"])
                return user

        user = User(
            email=email or None,
            google_id=google_id,
            is_email_verified=bool(email),
            full_name=claims.get("name", ""),
        )
        # Username stays null so onboarding still asks for one, exactly as
        # it does for every other signup route.
        user.set_unusable_password()
        user.save()
        return user
