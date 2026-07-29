from django.contrib import admin

from apps.bot.models import TelegramProfile


@admin.register(TelegramProfile)
class TelegramProfileAdmin(admin.ModelAdmin):
    list_display = ("telegram_username", "telegram_user_id", "user", "awaiting_action", "created_at")
    search_fields = ("telegram_username", "telegram_user_id", "user__email")
    readonly_fields = ("telegram_user_id", "created_at", "updated_at")
