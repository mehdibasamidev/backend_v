from django.contrib import admin

from apps.bot.models import TelegramProfile


class RegistrationStatusFilter(admin.SimpleListFilter):
    """
    A profile with no linked user is someone who ran /start and never sent a
    valid invite code.

    Written as a SimpleListFilter rather than putting "user__isnull" in
    list_filter: that entry has to name a real field, and a lookup there
    fails the system check at startup (admin.E116).
    """
    title = "registration"
    parameter_name = "registered"

    def lookups(self, request, model_admin):
        return [
            ("yes", "Registered"),
            ("no", "Awaiting invite code"),
        ]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(user__isnull=False)
        if self.value() == "no":
            return queryset.filter(user__isnull=True)
        return queryset


@admin.register(TelegramProfile)
class TelegramProfileAdmin(admin.ModelAdmin):
    list_display = (
        "telegram_username",
        "telegram_user_id",
        "user",
        "is_registered",
        "awaiting_action",
        "created_at",
    )
    # A pile of unregistered profiles means codes are going out that don't
    # work - worth being able to filter for.
    list_filter = (RegistrationStatusFilter,)
    search_fields = (
        "telegram_username",
        "telegram_user_id",
        "user__email",
        "user__username",
    )
    readonly_fields = ("telegram_user_id", "created_at", "updated_at")
    raw_id_fields = ("user",)

    @admin.display(boolean=True, description="Registered")
    def is_registered(self, obj):
        return obj.user_id is not None
