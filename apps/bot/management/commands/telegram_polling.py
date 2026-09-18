import asyncio
import logging

from django.core.management.base import BaseCommand

from apps.bot.services.bot_app import build_application


logger = logging.getLogger("apps")


class Command(BaseCommand):
    help = (
        "Runs the Telegram bot using long polling instead of webhook."
    )

    def handle(self, *args, **options):
        """
        Django entry point.

        asyncio.run() creates the event loop needed by
        python-telegram-bot.
        """
        asyncio.run(self._run())

    async def _run(self):
        """
        Start the Telegram bot in long-polling mode.

        The bot itself continuously calls Telegram's getUpdates API.
        This means Telegram does NOT need to establish an inbound
        connection to our server.
        """

        # Build the application with polling enabled.
        #
        # Unlike webhook mode, this keeps python-telegram-bot's
        # built-in updater enabled.
        application = build_application(
            for_polling=True
        )

        # Telegram allows only one of webhook or getUpdates polling
        # to be active at a time.
        #
        # Remove the old webhook first.
        #
        # IMPORTANT:
        # drop_pending_updates=False means we KEEP updates that are
        # already waiting on Telegram.
        #
        # We previously saw pending updates, so we do NOT want to
        # throw them away.
        await application.bot.delete_webhook(
            drop_pending_updates=False
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Telegram polling started..."
            )
        )

        # Initialize/start the application.
        #
        # The async context manager takes care of the application's
        # lifecycle.
        async with application:
            await application.start()

            # Start Telegram's built-in long polling.
            #
            # The connection is:
            #
            # Django server
            #      |
            #      | outbound through TELEGRAM_PROXY_URL
            #      v
            # api.telegram.org
            #
            # Telegram no longer needs to connect inbound to
            # api.spacedigital.top.
            await application.updater.start_polling(
                allowed_updates=[
                    "message",
                    "callback_query",
                ],
                timeout=30,
            )

            try:
                # Keep this process alive indefinitely.
                #
                # Docker will restart the process if it exits,
                # because the telegram_bot service will use:
                #
                # restart: unless-stopped
                await asyncio.Event().wait()

            finally:
                # Stop polling cleanly when the process receives
                # a shutdown signal.
                await application.updater.stop()

                # Stop the application itself.
                await application.stop()
