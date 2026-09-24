from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.bot.models import TelegramBotSettings
from apps.bot.services.bot_settings import normalize_bot_username


class TelegramBotSettingsSerializer(serializers.ModelSerializer):
    """
    The admin panel's "Telegram bot" section. Only bot_username is
    writable; everything else is what the bot container recorded and what
    follows from it.
    """
    # Declared rather than generated: the model's max_length=32 would reject
    # a pasted https://t.me/<name> link before it is cut down to the name.
    bot_username = serializers.CharField(allow_blank=True)
    effective_bot_username = serializers.CharField(read_only=True)
    is_configured = serializers.BooleanField(read_only=True)
    matches_running_bot = serializers.BooleanField(read_only=True, allow_null=True)

    class Meta:
        model = TelegramBotSettings
        fields = [
            "bot_username",
            "detected_bot_username",
            "detected_at",
            "effective_bot_username",
            "is_configured",
            "matches_running_bot",
            "updated_at",
        ]
        read_only_fields = ["detected_bot_username", "detected_at", "updated_at"]

    def validate_bot_username(self, value):
        try:
            return normalize_bot_username(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
