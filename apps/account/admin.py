from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.account.models import AuthSettings, OtpCode, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        "username", "email", "phone_number",
        "is_email_verified", "is_phone_verified",
        "is_active", "is_staff", "date_joined",
    )
    list_filter = ("is_email_verified", "is_phone_verified", "is_active", "is_staff")
    search_fields = ("username", "email", "phone_number", "full_name")
    ordering = ("-date_joined",)
    readonly_fields = ("id", "identifier", "date_joined", "last_login")

    fieldsets = (
        (None, {"fields": ("id", "identifier", "password")}),
        ("Identifiers", {
            "fields": (
                "username", "email", "is_email_verified",
                "phone_number", "is_phone_verified",
            ),
        }),
        ("Profile", {"fields": ("full_name", "profile_picture", "biography", "google_id")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined", "last_seen")}),
    )
    # BaseUserAdmin's add form expects USERNAME_FIELD, which here is the
    # internal identifier nobody types - so give it a real one.
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "phone_number", "password1", "password2"),
        }),
    )


@admin.register(AuthSettings)
class AuthSettingsAdmin(admin.ModelAdmin):
    list_display = ("__str__", "email_otp_required", "updated_at")

    def has_add_permission(self, request):
        # Single row - get_solo() creates it; a second one would make
        # "which setting is live" ambiguous.
        return not AuthSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OtpCode)
class OtpCodeAdmin(admin.ModelAdmin):
    """
    Read-only. Present for support ("did the code actually go out?") and
    for spotting abuse patterns - never for editing, and the code itself is
    stored hashed so it can't be read out here either.
    """
    list_display = ("target", "purpose", "channel", "attempts", "is_used", "created_at", "expires_at")
    list_filter = ("purpose", "channel", "is_used")
    search_fields = ("target",)
    readonly_fields = [f.name for f in OtpCode._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
