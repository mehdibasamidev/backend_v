import asyncio
import logging
import time

from django.core.management.base import CommandError
from telegram.request import HTTPXRequest


logger = logging.getLogger("apps")


class PollingRequest(HTTPXRequest):
    """Track successful polls, including empty responses when nobody writes."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.last_success = time.monotonic()

    async def post(self, *args, **kwargs):
        result = await super().post(*args, **kwargs)
        self.last_success = time.monotonic()
        return result


async def wait_for_polling(stop, request, *, stale_after=180, check_interval=15):
    # Sending reminders uses a separate HTTP client and cannot prove that
    # getUpdates is still alive. Empty successful polls are its heartbeat.
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=check_interval)
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - request.last_success
            if elapsed >= stale_after:
                logger.error(
                    "No successful Telegram getUpdates response for %.0fs; "
                    "exiting so Docker restarts the connection. Check Telegram proxy connectivity.",
                    elapsed,
                )
                raise CommandError("Telegram polling stopped responding")
