from django.contrib.auth import get_user_model

from apps.bot.models import TelegramProfile

User = get_user_model()


def get_or_create_telegram_user(telegram_user) -> TelegramProfile:
    """
    telegram_user is a telegram.User object from an incoming Update.
    Auto-registers a Django User the first time someone starts the bot -
    Telegram users don't have emails, so we synthesize a unique, unusable one
    (same `username=None` convention already used by RegisterSerializer).
    """
    try:
        return TelegramProfile.objects.select_related("user").get(
            telegram_user_id=telegram_user.id
        )
    except TelegramProfile.DoesNotExist:
        pass

    django_user = User(
        email=f"tg{telegram_user.id}@telegram.local",
        username=None,
        full_name=" ".join(filter(None, [telegram_user.first_name, telegram_user.last_name])),
    )
    django_user.set_unusable_password()
    django_user.save()

    return TelegramProfile.objects.create(
        user=django_user,
        telegram_user_id=telegram_user.id,
        telegram_username=telegram_user.username or "",
        telegram_first_name=telegram_user.first_name or "",
    )
