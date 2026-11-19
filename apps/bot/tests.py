import asyncio
import time
from unittest.mock import AsyncMock, patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings
from telegram.error import NetworkError
from telegram.request import HTTPXRequest

from apps.bot.services.bot_app import build_application
from apps.bot.services.polling_health import PollingRequest, wait_for_polling


class PollingHealthTests(SimpleTestCase):
    async def test_empty_success_is_healthy_but_network_failure_is_not(self):
        request = PollingRequest()
        try:
            request.last_success = 0
            with patch.object(HTTPXRequest, "post", new=AsyncMock(return_value=[])):
                self.assertEqual(await request.post("unused"), [])
            successful_at = request.last_success
            self.assertGreater(successful_at, 0)
            with patch.object(HTTPXRequest, "post", new=AsyncMock(side_effect=NetworkError("offline"))):
                with self.assertRaises(NetworkError):
                    await request.post("unused")
            self.assertEqual(request.last_success, successful_at)
        finally:
            await request.shutdown()

    async def test_stalled_poll_exits_for_container_restart(self):
        request = PollingRequest()
        try:
            request.last_success = time.monotonic() - 181
            with self.assertRaisesMessage(CommandError, "Telegram polling stopped responding"):
                await wait_for_polling(asyncio.Event(), request, check_interval=0.001)
        finally:
            await request.shutdown()

    async def test_shutdown_does_not_trigger_restart(self):
        request = PollingRequest()
        try:
            stop = asyncio.Event()
            asyncio.get_running_loop().call_soon(stop.set)
            await wait_for_polling(stop, request, check_interval=0.001)
        finally:
            await request.shutdown()

    @override_settings(TELEGRAM_BOT_TOKEN="123:test", TELEGRAM_PROXY_URL="http://127.0.0.1:8080")
    async def test_polling_builder_preserves_proxy_and_tracks_request(self):
        with patch("apps.bot.services.bot_app.PollingRequest", wraps=PollingRequest) as factory:
            application = build_application(for_polling=True)
        self.assertEqual(factory.call_args.kwargs["proxy"], "http://127.0.0.1:8080")
        request = application.bot_data["polling_request"]
        try:
            self.assertIsInstance(request, PollingRequest)
        finally:
            await request.shutdown()
            await application.bot.request.shutdown()
