from django.urls import path

from apps.bot.views import telegram_webhook

urlpatterns = [
    path("bot/webhook/<str:webhook_secret>/", telegram_webhook, name="telegram-webhook"),
]
