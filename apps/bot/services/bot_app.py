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

from apps.bot.handlers import common, plans, custom_plan, subscriptions, payment, admin_review

_application: Application | None = None
_init_lock = asyncio.Lock()


def build_application() -> Application:
    application = (
        ApplicationBuilder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .updater(None)  # updates are fed in manually from the Django webhook view - no polling/built-in webserver
        .build()
    )

    application.add_handler(CommandHandler("start", common.start))
    application.add_handler(CallbackQueryHandler(common.show_main_menu, pattern=r"^menu:main$"))
    application.add_handler(CallbackQueryHandler(common.noop, pattern=r"^noop$"))

    application.add_handler(CallbackQueryHandler(plans.show_plan_list, pattern=r"^menu:plans$"))
    application.add_handler(CallbackQueryHandler(plans.show_plan_detail, pattern=r"^plan:view:"))
    application.add_handler(CallbackQueryHandler(plans.buy_plan, pattern=r"^plan:buy:"))

    application.add_handler(CallbackQueryHandler(custom_plan.start_builder, pattern=r"^custom:start$"))
    application.add_handler(CallbackQueryHandler(custom_plan.adjust, pattern=r"^cst:"))
    application.add_handler(CallbackQueryHandler(custom_plan.confirm, pattern=r"^cstok:"))

    application.add_handler(CallbackQueryHandler(subscriptions.list_subscriptions, pattern=r"^menu:subscriptions$"))

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
