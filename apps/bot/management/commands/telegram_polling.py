import asyncio
import contextlib
import logging
import signal

from django.core.management.base import BaseCommand

from apps.bot.services.bot_app import build_application
from apps.bot.services.bot_settings import arecord_running_bot_until_done
from apps.bot.services.proof_notifier import run_proof_notifier


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

            # Posts receipts submitted in the app to the admins. It lives
            # here, not in the web container, because this is the process
            # that owns the Telegram connection. Webhook mode has no
            # equivalent, so app receipts are only announced while polling.
            notifier = asyncio.create_task(
                run_proof_notifier(application.bot)
            )

            # initialize() (entering the block) ran getMe, so the bot knows
            # its own username. Recorded for the web container, which
            # builds the "Login with Telegram" links and never talks to
            # Telegram itself. A task rather than awaited here: on a fresh
            # install the web container is usually still migrating, and
            # this retries until the table is there while the bot already
            # serves. Never fatal.
            username_recorder = asyncio.create_task(
                arecord_running_bot_until_done(application.bot)
            )

            # docker-compose starts this command in exec form, so Python
            # is PID 1, and the kernel drops a SIGTERM that PID 1 has no
            # handler for: `docker stop` would wait out its 10 seconds and
            # SIGKILL, and the finally below would never run. That finally
            # is what lets the notifier hand back a receipt it was halfway
            # through posting. (SIGINT / Ctrl+C is already turned into a
            # cancellation by asyncio.run.)
            stop = asyncio.Event()
            with contextlib.suppress(NotImplementedError):  # Windows
                asyncio.get_running_loop().add_signal_handler(
                    signal.SIGTERM, stop.set
                )

            try:
                # Keep this process alive until docker stops it.
                #
                # Docker will restart the process if it exits
                # on its own, because the telegram_bot service uses:
                #
                # restart: unless-stopped
                await stop.wait()

            finally:
                username_recorder.cancel()
                notifier.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await username_recorder
                with contextlib.suppress(asyncio.CancelledError):
                    await notifier

                # Stop polling cleanly when the process receives
                # a shutdown signal.
                await application.updater.stop()

                # Stop the application itself.
                await application.stop()
