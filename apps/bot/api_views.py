"""
REST side of "Login with Telegram" and "Connect Telegram", and the admin
panel's bot settings. The logic is in apps/bot/services/telegram_auth.py,
shared with the bot's handlers, and apps/bot/services/bot_settings.py.

Kept apart from apps/bot/views.py, which is the Telegram webhook and has
nothing to do with the app's API.
"""

from drf_yasg.utils import no_body, swagger_auto_schema
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.bot.serializers.bot_settings import TelegramBotSettingsSerializer
from apps.bot.serializers.telegram_auth import (
    TelegramAuthStartSerializer,
    TelegramLinkStatusSerializer,
    TelegramLoginPollResultSerializer,
    TelegramLoginPollSerializer,
    TelegramLoginStartSerializer,
)
from apps.bot.services.bot_settings import get_bot_settings, set_admin_bot_username
from apps.bot.services.telegram_auth import (
    link_status,
    poll_login,
    start_link_request,
    start_login_request,
)
from config.utils.custom_serializers import create_response_serializer
from config.utils.response import BadRequestResponse, SuccessResponse


class _PerClientIpThrottle(AnonRateThrottle):
    """
    Per client IP, signed in or not.

    AnonRateThrottle skips authenticated callers, and these endpoints are
    open to both - a token must not be the way around the limit.

    The client IP is the LAST X-Forwarded-For entry: HAProxy appends the
    address it saw and keeps whatever the client sent in front of it, so
    DRF's default (the whole header) would give every forged header a
    fresh bucket. Without the header (no proxy, local runs) it is
    REMOTE_ADDR.

    Counts live in the default cache, which is per process (no CACHES
    setting), so with N gunicorn workers the real ceiling is up to N times
    the rate.
    """

    def get_cache_key(self, request, view):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        ident = forwarded.split(",")[-1].strip() or request.META.get("REMOTE_ADDR", "")
        return self.cache_format % {"scope": self.scope, "ident": ident}


class TelegramLoginStartThrottle(_PerClientIpThrottle):
    # Every call writes a row; this caps what one client can fill the
    # table with before the one-day purge.
    scope = "telegram_login_start"
    rate = "10/min"


class TelegramLoginPollThrottle(_PerClientIpThrottle):
    # The app polls every couple of seconds; this leaves room for that and
    # nothing like enough to guess a poll secret.
    scope = "telegram_login_poll"
    rate = "60/min"


class TelegramLoginStartView(APIView):
    """
    "Login with Telegram", step 1: a deep link to the bot and the code to
    pick there. The app then polls with request_id + poll_secret.
    """
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]
    throttle_classes = [TelegramLoginStartThrottle]

    @swagger_auto_schema(
        request_body=no_body,
        responses={200: create_response_serializer(
            data_serializer_class=TelegramLoginStartSerializer,
            text_message="Open the link in Telegram and pick the code shown in the app.",
        )},
    )
    def post(self, request):
        auth_request, deep_link, poll_secret = start_login_request()
        return SuccessResponse(
            data=TelegramLoginStartSerializer(
                auth_request,
                context={"deep_link": deep_link, "poll_secret": poll_secret},
            ).data,
            message="Open the link in Telegram and pick the code shown in the app.",
        )


class TelegramLoginPollView(APIView):
    """
    "Login with Telegram", step 2, repeated until the status is final. The
    response that finds the request confirmed carries the session; it is
    handed out once.
    """
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]
    throttle_classes = [TelegramLoginPollThrottle]

    @swagger_auto_schema(
        request_body=TelegramLoginPollSerializer,
        responses={200: create_response_serializer(
            data_serializer_class=TelegramLoginPollResultSerializer,
            text_message="Sign-in request status.",
        )},
    )
    def post(self, request):
        serializer = TelegramLoginPollSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        return SuccessResponse(
            data=poll_login(
                serializer.validated_data["request_id"],
                serializer.validated_data["poll_secret"],
            ),
            message="Sign-in request status.",
        )


class TelegramLinkStartView(APIView):
    """"Connect Telegram" from the profile: a deep link and the code to pick in the bot."""
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]

    @swagger_auto_schema(
        request_body=no_body,
        responses={200: create_response_serializer(
            data_serializer_class=TelegramAuthStartSerializer,
            text_message="Open the link in Telegram and pick the code shown in the app.",
        )},
    )
    def post(self, request):
        auth_request, deep_link = start_link_request(request.user)
        return SuccessResponse(
            data=TelegramAuthStartSerializer(
                auth_request, context={"deep_link": deep_link},
            ).data,
            message="Open the link in Telegram and pick the code shown in the app.",
        )


class TelegramLinkStatusView(APIView):
    """Polled by the app until the link is final. Only the requester's own requests."""
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]

    @swagger_auto_schema(
        responses={200: create_response_serializer(
            data_serializer_class=TelegramLinkStatusSerializer,
            text_message="Telegram link status.",
        )},
    )
    def get(self, request, request_id):
        return SuccessResponse(
            data=link_status(request_id, request.user),
            message="Telegram link status.",
        )


class AdminTelegramBotSettingsView(APIView):
    """
    The admin panel's "Telegram bot" section (Settings tab): the username
    the deep links use. GET and PATCH only - the row is created by
    migration bot 0006 and never deleted. Only bot_username is writable;
    the detected fields belong to the bot container.
    """
    permission_classes = [IsAdminUser]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(
        responses={200: create_response_serializer(
            data_serializer_class=TelegramBotSettingsSerializer,
            text_message="Telegram bot settings retrieved successfully.",
        )},
    )
    def get(self, request):
        return SuccessResponse(
            data=TelegramBotSettingsSerializer(get_bot_settings()).data,
            message="Telegram bot settings retrieved successfully.",
        )

    @swagger_auto_schema(
        request_body=TelegramBotSettingsSerializer,
        responses={200: create_response_serializer(
            data_serializer_class=TelegramBotSettingsSerializer,
            text_message="Telegram bot settings updated.",
        )},
    )
    def patch(self, request):
        row = get_bot_settings()
        serializer = TelegramBotSettingsSerializer(row, data=request.data, partial=True)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)
        # Not serializer.save(): the service writes bot_username alone, so
        # this can't write back detected_* values the bot container
        # replaced since the row was read.
        if "bot_username" in serializer.validated_data:
            row = set_admin_bot_username(serializer.validated_data["bot_username"])
        return SuccessResponse(
            data=TelegramBotSettingsSerializer(row).data,
            message="Telegram bot settings updated.",
        )
