from django import forms
from django.contrib import admin

from apps.bot.models import TelegramAuthRequest, TelegramBotSettings, TelegramProfile
from apps.bot.services.bot_settings import normalize_bot_username


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


class TelegramBotSettingsForm(forms.ModelForm):
    # Declared rather than generated: the model's max_length=32 would reject
    # a pasted https://t.me/<name> link before clean_bot_username cuts it
    # down to the name.
    bot_username = forms.CharField(
        required=False,
        max_length=255,
        label="Bot username",
        help_text=(
            "The bot's username, e.g. my_vpn_bot (\"@name\" or a t.me link work "
            "too). Leave empty to use the detected one below, which the bot "
            "container records from its own token every time it starts. A value "
            "entered here always wins and is never overwritten by the bot."
        ),
    )

    class Meta:
        model = TelegramBotSettings
        fields = ["bot_username"]

    def clean_bot_username(self):
        return normalize_bot_username(self.cleaned_data["bot_username"])


@admin.register(TelegramBotSettings)
class TelegramBotSettingsAdmin(admin.ModelAdmin):
    """
    The bot username "Login with Telegram" and "Connect Telegram" links
    use. Same row as the admin panel's Settings tab.
    """
    form = TelegramBotSettingsForm
    list_display = (
        "__str__",
        "bot_username",
        "detected_bot_username",
        "match_display",
        "detected_at",
        "updated_at",
    )
    fields = (
        "bot_username",
        "detected_bot_username",
        "detected_at",
        "effective_display",
        "match_display",
        "updated_at",
    )
    readonly_fields = (
        "detected_bot_username",
        "detected_at",
        "effective_display",
        "match_display",
        "updated_at",
    )

    @admin.display(description="Used for the links")
    def effective_display(self, obj):
        if obj.effective_bot_username:
            return f"@{obj.effective_bot_username}"
        return "Nothing yet - Login with Telegram and Connect Telegram answer 400"

    @admin.display(boolean=True, description="Matches the running bot")
    def match_display(self, obj):
        # Unknown (None) until both the admin value and the detected one exist.
        return obj.matches_running_bot

    def has_add_permission(self, request):
        # Single row - migration bot 0006 creates it; a second would make
        # "which setting is live" ambiguous.
        return not TelegramBotSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if change:
            # Only the admin's field: a full save would write back the
            # detected_* values read when the page was opened, undoing a
            # bot container start in between.
            obj.save(update_fields=["bot_username", "updated_at"])
        else:
            super().save_model(request, obj, form, change)
