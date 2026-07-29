import asyncio

from django.conf import settings
from django.core.management.base import BaseCommand
from telegram import Bot


class Command(BaseCommand):
    help = "Registers the Telegram webhook URL with the Bot API. Run once per deploy or whenever the token/URL changes."

    def handle(self, *args, **options):
        asyncio.run(self._set_webhook())

    async def _set_webhook(self):
        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        url = f"{settings.TELEGRAM_BASE_WEBHOOK_URL.rstrip('/')}/bot/webhook/{settings.TELEGRAM_WEBHOOK_SECRET}/"

        await bot.set_webhook(
            url=url,
            secret_token=settings.TELEGRAM_WEBHOOK_SECRET,
            allowed_updates=["message", "callback_query"],
        )
        info = await bot.get_webhook_info()

        self.stdout.write(self.style.SUCCESS(f"Webhook set to: {url}"))
        self.stdout.write(str(info.to_dict()))
