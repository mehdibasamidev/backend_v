import uuid

from django.conf import settings
from django.db import models


class TelegramProfile(models.Model):
    """
    Links a Telegram user to a Django User account so the bot can reuse all
    the same VPN purchase/payment/provisioning logic as the REST API.

    `awaiting_action` is a small piece of state persisted in the DB (not in
    process memory) so the "next photo/text this user sends is a payment
    receipt for subscription X" flow survives bot restarts and works
    correctly even if you ever run multiple webhook workers.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="telegram_profile",
    )
    telegram_user_id = models.BigIntegerField(unique=True)
    telegram_username = models.CharField(max_length=64, blank=True)
    telegram_first_name = models.CharField(max_length=150, blank=True)

    # e.g. "receipt:<subscription_id>" while we're waiting for a payment proof.
    # Blank means "not waiting for anything in particular".
    awaiting_action = models.CharField(max_length=100, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"@{self.telegram_username or self.telegram_user_id}"

    def set_awaiting_action(self, action: str):
        self.awaiting_action = action
        self.save(update_fields=["awaiting_action", "updated_at"])

    def clear_awaiting_action(self):
        if self.awaiting_action:
            self.awaiting_action = ""
            self.save(update_fields=["awaiting_action", "updated_at"])
