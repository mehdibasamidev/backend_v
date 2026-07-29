import json
import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from telegram import Update

from apps.bot.services.bot_app import get_application

logger = logging.getLogger(__name__)


@csrf_exempt
async def telegram_webhook(request, webhook_secret):
    # Layer 1: the URL itself is unguessable (random path secret).
    if webhook_secret != settings.TELEGRAM_WEBHOOK_SECRET:
        return HttpResponseForbidden("invalid path")

    # Layer 2: Telegram's own secret_token header (set via setWebhook) - proves
    # the request actually came from Telegram, even if the URL ever leaks.
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != settings.TELEGRAM_WEBHOOK_SECRET:
        return HttpResponseForbidden("invalid secret token")

    if request.method != "POST":
        return HttpResponse(status=405)

    try:
        data = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return HttpResponse(status=400)

    application = await get_application()
    update = Update.de_json(data, application.bot)

    try:
        await application.process_update(update)
    except Exception:
        # Never let a handler bug surface as a 5xx - Telegram will just keep
        # retrying the exact same update otherwise. Log it and ack instead.
        logger.exception("Error while processing Telegram update %s", getattr(update, "update_id", "?"))

    return HttpResponse(status=200)
