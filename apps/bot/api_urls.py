from django.urls import path

from apps.bot.api_views import (
    AdminTelegramBotSettingsView,
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

    # Admin panel, Settings tab: the bot username the deep links above use.
    path("bot/admin/settings/", AdminTelegramBotSettingsView.as_view(), name="bot-admin-settings"),
]
