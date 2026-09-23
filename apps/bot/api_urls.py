from django.urls import path

from apps.bot.api_views import (
    TelegramLinkStartView,
    TelegramLinkStatusView,
    TelegramLoginPollView,
    TelegramLoginStartView,
)

# Mounted under api/v1/ by config/urls.py. apps/bot/urls.py stays the
# Telegram webhook only.
urlpatterns = [
    # Sign-in screen: "Login with Telegram".
    path("auth/telegram/login/", TelegramLoginStartView.as_view(), name="auth-telegram-login"),
    path("auth/telegram/login/poll/", TelegramLoginPollView.as_view(), name="auth-telegram-login-poll"),

    # Profile: "Connect Telegram".
    path("auth/telegram/link/", TelegramLinkStartView.as_view(), name="auth-telegram-link"),
    path(
        "auth/telegram/link/<uuid:request_id>/",
        TelegramLinkStatusView.as_view(),
        name="auth-telegram-link-status",
    ),
]
