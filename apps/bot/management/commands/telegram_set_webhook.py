import asyncio

from django.conf import settings
from django.core.management.base import BaseCommand
from telegram import Bot
from telegram.request import HTTPXRequest


class Command(BaseCommand):
    help = (
        "Registers the Telegram webhook URL with the Bot API. Run once per "
        "deploy, or whenever the token or the public URL changes."
    )

    def handle(self, *args, **options):
        asyncio.run(self._set_webhook())

    async def _set_webhook(self):
        # This command is an OUTBOUND call to api.telegram.org, so it needs
        # the same proxy the bot itself uses. Without it, on a host that
        # can't reach Telegram directly, this fails while the webhook view
        # looks perfectly healthy - the two directions are independent.
        proxy = getattr(settings, "TELEGRAM_PROXY_URL", "")
        request = HTTPXRequest(
            proxy=proxy or None,
            connect_timeout=20.0,
            read_timeout=20.0,
        )

        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN, request=request)
        url = (
            f"{settings.TELEGRAM_BASE_WEBHOOK_URL.rstrip('/')}"
            f"/bot/webhook/{settings.TELEGRAM_WEBHOOK_SECRET}/"
        )

        await bot.set_webhook(
            url=url,
            secret_token=settings.TELEGRAM_WEBHOOK_SECRET,
            allowed_updates=["message", "callback_query"],
        )
        info = await bot.get_webhook_info()

        self.stdout.write(self.style.SUCCESS(f"Webhook set to: {url}"))
        self.stdout.write(str(info.to_dict()))

        # Surfaced rather than left in the dict: this is the field that says
        # whether Telegram can actually reach the server, and it's the first
        # thing worth knowing after a deploy.
        if info.last_error_message:
            self.stdout.write(
                self.style.WARNING(f"Last error: {info.last_error_message}")
            )
