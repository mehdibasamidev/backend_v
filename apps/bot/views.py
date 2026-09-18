import json
import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden
from telegram import Update

from apps.bot.services.bot_app import get_application

logger = logging.getLogger(__name__)


async def telegram_webhook(request, webhook_secret):
    """
    Telegram webhook endpoint.

    Security is handled in two layers:

    1. The webhook URL contains a random secret path.
    2. Telegram sends the same secret in the
       X-Telegram-Bot-Api-Secret-Token header.

    The view is intentionally async because python-telegram-bot's
    Application API is asynchronous.
    """

    # Layer 1: the URL itself is unguessable (random path secret).
    if webhook_secret != settings.TELEGRAM_WEBHOOK_SECRET:
        return HttpResponseForbidden("invalid path")

    # Layer 2: Telegram's own secret_token header (set via setWebhook).
    # This proves the request actually came from Telegram, even if the URL
    # ever leaks.
    if (
        request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        != settings.TELEGRAM_WEBHOOK_SECRET
    ):
        return HttpResponseForbidden("invalid secret token")

    # Telegram sends webhook updates using POST.
    if request.method != "POST":
        return HttpResponse(status=405)

    # Parse Telegram's JSON payload.
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return HttpResponse(status=400)

    # Get the shared Telegram Application instance.
    application = await get_application()

    # Convert Telegram's JSON payload into a python-telegram-bot Update.
    update = Update.de_json(data, application.bot)

    try:
        # Process the update through the registered Telegram handlers.
        await application.process_update(update)

    except Exception:
        # Never let a handler bug surface as a 5xx response.
        # Telegram will retry a webhook update when it receives a 5xx,
        # which can cause the same broken update to be delivered repeatedly.
        logger.exception(
            "Error while processing Telegram update %s",
            getattr(update, "update_id", "?"),
        )

    # Acknowledge the webhook request to Telegram.
    return HttpResponse(status=200)


# Django's CSRF middleware checks this attribute directly.
#
# We intentionally do NOT use:
#
#     @csrf_exempt
#
# because in this Django setup that decorator wraps the async view in a
# synchronous wrapper. Django then receives an unawaited coroutine instead
# of an HttpResponse, resulting in:
#
#     ValueError: The view ... didn't return an HttpResponse object.
#     It returned an unawaited coroutine instead.
#
# Setting the attribute directly keeps telegram_webhook as a true async view.
telegram_webhook.csrf_exempt = True
