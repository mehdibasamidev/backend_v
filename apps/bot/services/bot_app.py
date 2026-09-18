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
)

_application: Application | None = None
_init_lock = asyncio.Lock()


def build_application() -> Application:
    builder = (
        ApplicationBuilder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .updater(None)  # updates are fed in manually from the Django webhook view - no polling/built-in webserver
    )

    # Outbound calls to api.telegram.org are what actually need this, not the
    # webhook: Telegram reaching us is inbound and unaffected, but every reply
    # the bot sends is a request FROM this server. On a host where that
    # destination is unreachable, the bot receives everything and answers
    # nothing - which looks exactly like the bot being dead.
    proxy = getattr(settings, "TELEGRAM_PROXY_URL", "")
    if proxy:
        builder = builder.proxy(proxy).get_updates_proxy(proxy)

    # Telegram's own timeouts are short; a proxied hop is slower than a direct
    # one, and the default read timeout will cut replies off part-way.
    builder = (
        builder
        .connect_timeout(20.0)
        .read_timeout(20.0)
        .write_timeout(20.0)
    )

    application = builder.build()

    application.add_handler(CommandHandler("start", common.start))
    application.add_handler(CommandHandler("mycode", referral.my_referral_code))
    application.add_handler(CallbackQueryHandler(common.show_main_menu, pattern=r"^menu:main$"))
    application.add_handler(CallbackQueryHandler(common.noop, pattern=r"^noop$"))

    application.add_handler(CallbackQueryHandler(plans.show_plan_list, pattern=r"^menu:plans$"))
    application.add_handler(CallbackQueryHandler(plans.show_plan_detail, pattern=r"^plan:view:"))
    application.add_handler(CallbackQueryHandler(plans.buy_plan, pattern=r"^plan:buy:"))

    application.add_handler(CallbackQueryHandler(custom_plan.start_builder, pattern=r"^custom:start$"))
    application.add_handler(CallbackQueryHandler(custom_plan.adjust, pattern=r"^cst:"))
    application.add_handler(CallbackQueryHandler(custom_plan.confirm, pattern=r"^cstok:"))

    application.add_handler(CallbackQueryHandler(subscriptions.list_subscriptions, pattern=r"^menu:subscriptions$"))

    # One text handler for everything: receive_receipt checks for a pending
    # invite code before treating the message as a payment receipt. Two
    # separate MessageHandlers would both fire on the same update.
    application.add_handler(MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), payment.receive_receipt))

    application.add_handler(CallbackQueryHandler(admin_review.review, pattern=r"^review:(approve|reject):"))

    return application


async def get_application() -> Application:
    """
    Lazily builds + initializes a single Application per worker process.
    initialize() only sets up the bot/HTTP client - it does NOT start
    polling or a webhook server; Django's webhook view feeds updates in
    directly via application.process_update().
    """
    global _application
    if _application is not None:
        return _application
    async with _init_lock:
        if _application is None:
            _application = build_application()
            await _application.initialize()
    return _application


async def shutdown_application():
    global _application
    if _application is not None:
        await _application.shutdown()
        _application = None
