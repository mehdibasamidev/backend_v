import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


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
    # Nullable because a profile now exists BEFORE the account does: when
    # invite codes are required, /start records who is being asked for one
    # so their next message is read as that code. Creating the User first
    # and deleting it on a bad code would leave orphans behind every typo.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="telegram_profile",
        null=True,
        blank=True,
    )
    telegram_user_id = models.BigIntegerField(unique=True)
    telegram_username = models.CharField(max_length=64, blank=True)
    telegram_first_name = models.CharField(max_length=150, blank=True)

    # e.g. "checkout:fixed:<plan_id>" while we're waiting for a payment
    # proof, or "awaiting_referral_code" during a gated signup.
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


class TelegramAuthPurpose(models.TextChoices):
    LOGIN = "login", "Sign in to the app"
    LINK = "link", "Connect to an app account"


class TelegramAuthStatus(models.TextChoices):
    PENDING = "pending", "Waiting for the bot"
    CONFIRMED = "confirmed", "Confirmed in the bot"
    # Login only: the session has been handed to the app, so a second poll
    # can't collect another one.
    USED = "used", "Session handed out"
    CANCELLED = "cancelled", "Cancelled"
    FAILED = "failed", "Failed"


class TelegramAuthRequest(models.Model):
    """
    One "Login with Telegram" or "Connect Telegram" attempt, from the app
    asking for it to the bot confirming it (apps/bot/services/telegram_auth.py).

    Ownership of the Telegram account is proven by opening the deep link
    https://t.me/<bot>?start=<purpose>_<start_token> in it and picking, in
    the bot, the code the app is showing. The code step is what stops a
    phishing link - someone sending you THEIR deep link - from signing them
    into your account or moving your services to them: they can send the
    link, but they can't pick for you.

    "expired" is not stored: it is a pending request past expires_at, so no
    job has to sweep rows into it.

    A confirmed link and a login whose session was handed out are kept for
    good (see RETENTION): they are the record of who signed in as whom and
    of services moving between accounts.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purpose = models.CharField(max_length=10, choices=TelegramAuthPurpose.choices)

    # sha256 of the deep-link payload after "<purpose>_". The token itself
    # is only in the deep link handed to the app: while pending it is a
    # live key to an account, so a leaked table must not hand it over.
    # Telegram passes it back verbatim in "/start <payload>" and the bot
    # looks the request up by its hash.
    start_token_hash = models.CharField(max_length=64, unique=True)

    # Login only: sha256 of the secret the app polls with, which is returned
    # once and never stored. Blank for link requests - those are polled by
    # the signed-in account that owns them.
    poll_secret_hash = models.CharField(max_length=64, blank=True)

    # Shown in the app; the bot offers it among two decoys. Kept readable:
    # the bot needs it to draw the buttons, and it is useless without the
    # start token.
    code = models.CharField(max_length=4)

    # Link: the app account that asked. Login: the account signed into,
    # set once the bot confirms.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="telegram_auth_requests",
    )

    # Whoever opened the deep link first. Only they may press the code
    # buttons, so a forwarded confirmation can't be answered by someone else.
    opened_by_telegram_id = models.BigIntegerField(null=True, blank=True)
    # Whoever confirmed (or cancelled) it.
    telegram_user_id = models.BigIntegerField(null=True, blank=True)

    status = models.CharField(
        max_length=10,
        choices=TelegramAuthStatus.choices,
        default=TelegramAuthStatus.PENDING,
    )
    # Shown to the app as the reason for cancelled / failed.
    failure_reason = models.CharField(max_length=255, blank=True)
    # Link only: services that came over from a merged bot-only account.
    moved_subscriptions = models.PositiveIntegerField(default=0)
    # Link only: the bot-only account merged into `user` and deactivated.
    # The only link in the database between that account and whoever took
    # its paid services. PROTECT, because that account is kept as payment
    # evidence and must never be deleted.
    merged_from = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )

    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    TTL = timedelta(minutes=10)
    # Requests that never went through (pending, cancelled, failed, or a
    # login confirmed but never collected) are deleted once older than
    # this, whenever a new request is started. Confirmed links and used
    # logins are kept.
    RETENTION = timedelta(days=1)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.purpose} {self.effective_status} ({self.id})"

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def effective_status(self):
        """The status the app sees: a pending request past its TTL is "expired"."""
        if self.status == TelegramAuthStatus.PENDING and self.is_expired:
            return "expired"
        return self.status


class TelegramBotSettings(models.Model):
    """
    The bot's @username, which "Login with Telegram" and "Connect Telegram"
    build their https://t.me/<bot>?start=... deep links from
    (apps/bot/services/telegram_auth.py). Single row - use get_solo().
    Reads and writes go through apps/bot/services/bot_settings.py.

    Two fields because two parties know it. An admin types bot_username (the
    admin panel's Settings tab, or the Django admin). The bot container
    records detected_bot_username every time it starts, from getMe - the web
    container never talks to Telegram, so it can't find out by itself.

    The bot writing into bot_username instead would overwrite what the admin
    typed, and a value it had written once would go stale when the token
    changes. Kept apart, the admin's value always wins, and the detected one
    always follows whichever token the bot last started with. When both are
    set and differ, the links follow the admin's value and
    matches_running_bot is False, which the admin panel shows.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Stored without the "@". Telegram usernames are at most 32 characters.
    bot_username = models.CharField(
        max_length=32,
        blank=True,
        help_text=(
            "The bot's username without '@', e.g. my_vpn_bot. Leave empty to "
            "use the one the bot container detects when it starts. When set, "
            "it is used even if the running bot is a different one."
        ),
    )

    # Written only by the bot container (bot_settings.record_running_bot).
    # editable=False keeps them out of every form and serializer input.
    detected_bot_username = models.CharField(
        max_length=32,
        blank=True,
        editable=False,
        help_text="Recorded by the bot container from its own token each time it starts.",
    )
    detected_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        help_text="When the bot container last recorded its username.",
    )

    # The last admin change. A bot restart only moves detected_at.
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Telegram Bot Settings"
        verbose_name_plural = "Telegram Bot Settings"

    def __str__(self):
        username = self.effective_bot_username
        return f"Telegram bot settings (@{username})" if username else (
            "Telegram bot settings (not configured)"
        )

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(defaults={})
        return obj

    @property
    def effective_bot_username(self):
        """What the deep links use: the admin's value, else the detected one, else ""."""
        return self.bot_username or self.detected_bot_username

    @property
    def is_configured(self):
        return bool(self.effective_bot_username)

    @property
    def matches_running_bot(self):
        """
        None unless both are known - with only one of them there is nothing
        to compare. Case-insensitive, as Telegram usernames are.
        """
        if not (self.bot_username and self.detected_bot_username):
            return None
        return self.bot_username.lower() == self.detected_bot_username.lower()
