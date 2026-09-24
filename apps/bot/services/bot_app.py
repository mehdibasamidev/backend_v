import asyncio

from django.conf import settings
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from apps.bot.handlers import (
    common,
    plans,
    custom_plan,
    subscriptions,
    payment,
    admin_review,
    referral,
    telegram_auth,
)
from apps.bot.services.bot_settings import arecord_running_bot


_application: Application | None = None
_init_lock = asyncio.Lock()


def build_application(*, for_polling: bool = False) -> Application:
    """
    Build the Telegram application.

    There are two modes:

    1. Webhook mode:
       - for_polling=False
       - The built-in Telegram updater is disabled.
       - Django receives Telegram webhook requests and manually feeds
         updates into application.process_update().

    2. Polling mode:
       - for_polling=True
       - The built-in Telegram updater remains enabled.
       - telegram_polling management command calls start_polling().
       - The application itself continuously calls Telegram's
         getUpdates API.

    The same handlers are used in both modes.
    """

    builder = ApplicationBuilder().token(
        settings.TELEGRAM_BOT_TOKEN
    )

    # In webhook mode, Django is responsible for receiving updates.
    # Therefore, python-telegram-bot's built-in updater must be disabled.
    #
    # In polling mode, we DO NOT disable the updater because
    # start_polling() needs it.
    if not for_polling:
        builder = builder.updater(None)

    # Outbound calls to api.telegram.org need the proxy on this server.
    #
    # This is especially important for polling mode because getUpdates()
    # is an outbound connection from this server to Telegram.
    #
    # The same proxy is also used when the bot sends messages, answers
    # callback queries, uploads files, etc.
    proxy = getattr(settings, "TELEGRAM_PROXY_URL", "")

    if proxy:
        builder = (
            builder
            .proxy(proxy)
            .get_updates_proxy(proxy)
        )

    # Telegram API requests can take longer when going through a proxy,
    # so use explicit timeouts instead of relying on short defaults.
    builder = (
        builder
        .connect_timeout(20.0)
        .read_timeout(20.0)
        .write_timeout(20.0)
    )

    application = builder.build()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            common.start,
        )
    )

    application.add_handler(
        CommandHandler(
            "mycode",
            referral.my_referral_code,
        )
    )

    # ------------------------------------------------------------------
    # Main menu
    # ------------------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            common.show_main_menu,
            pattern=r"^menu:main$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            common.noop,
            pattern=r"^noop$",
        )
    )

    # ------------------------------------------------------------------
    # Plans
    # ------------------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            plans.show_plan_list,
            pattern=r"^menu:plans$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            plans.show_plan_detail,
            pattern=r"^plan:view:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            plans.buy_plan,
            pattern=r"^plan:buy:",
        )
    )

    # ------------------------------------------------------------------
    # Custom plan
    # ------------------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            custom_plan.start_builder,
            pattern=r"^custom:start$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            custom_plan.adjust,
            pattern=r"^cst:",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            custom_plan.confirm,
            pattern=r"^cstok:",
        )
    )

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            subscriptions.list_subscriptions,
            pattern=r"^menu:subscriptions$",
        )
    )

    # ------------------------------------------------------------------
    # Referral
    # ------------------------------------------------------------------

    application.add_handler(
            CallbackQueryHandler(
                referral.my_referral_code,
                pattern=r"^menu:referral$",
            )
        )

    # ------------------------------------------------------------------
    # Login with Telegram / Connect Telegram (the code buttons; the deep
    # link itself arrives through /start above)
    # ------------------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            telegram_auth.handle_auth_callback,
            pattern=r"^tga:",
        )
    )

    # ------------------------------------------------------------------
    # Payment receipt / normal text messages
    # ------------------------------------------------------------------
    #
    # One MessageHandler handles both:
    #
    #   - photos
    #   - normal text messages except commands
    #
    # payment.receive_receipt() is responsible for deciding what the
    # incoming message means, including checking for a pending invite code
    # before treating a message as a payment receipt.
    #
    # Keeping this as one handler avoids having multiple MessageHandlers
    # accidentally process the same update.
    #

    application.add_handler(
        MessageHandler(
            filters.PHOTO
            | (filters.TEXT & ~filters.COMMAND),
            payment.receive_receipt,
        )
    )

    # ------------------------------------------------------------------
    # Admin payment review
    # ------------------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            admin_review.review,
            pattern=r"^review:(approve|reject):",
        )
    )

    return application


async def get_application() -> Application:
    """
    Lazily build and initialize a single Application per worker process.

    This function is used by the Django webhook path.

    Important:
        initialize() only initializes the Telegram application and its
        HTTP client. It does NOT start polling.

    In webhook mode, Django receives the HTTP request and then calls
    application.process_update() manually.
    """

    global _application

    if _application is not None:
        return _application

    async with _init_lock:
        if _application is None:
            _application = build_application(
                for_polling=False
            )

            await _application.initialize()

            # Same as the polling command: initialize() ran getMe, so record
            # which bot this token is for the deep links. Never raises.
            await arecord_running_bot(_application.bot)

    return _application


async def shutdown_application():
    """
    Shut down the globally cached webhook application.

    This remains here because we are not deleting the existing webhook
    implementation yet.

    Once polling is fully verified in production, the old webhook-specific
    code can be cleaned up separately.
    """

    global _application

    if _application is not None:
        await _application.shutdown()
        _application = None
