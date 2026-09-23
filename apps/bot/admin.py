from django.contrib import admin

from apps.bot.models import TelegramAuthRequest, TelegramProfile


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


@admin.register(TelegramAuthRequest)
class TelegramAuthRequestAdmin(admin.ModelAdmin):
    """
    "Login with Telegram" / "Connect Telegram" attempts, for answering "why
    did my services move" or "who signed in as me". Read-only: this is
    evidence. Confirmed links (with the merged account, when there was one)
    and used logins are kept; attempts that never went through are purged
    a day after they were created.
    """
    list_display = (
        "created_at",
        "purpose",
        "status_display",
        "user",
        "telegram_user_id",
        "moved_subscriptions",
        "failure_reason",
    )
    list_filter = ("purpose", "status")
    search_fields = (
        "id",
        "telegram_user_id",
        "opened_by_telegram_id",
        "user__email",
        "user__phone_number",
        "user__username",
    )
    # The code and the two hashes are only meaningful to the flow itself.
    exclude = ("start_token_hash", "poll_secret_hash", "code")

    @admin.display(description="Status")
    def status_display(self, obj):
        # Shows "expired" for a pending row past its TTL, as the app sees it.
        return obj.effective_status

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
