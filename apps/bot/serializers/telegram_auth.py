from rest_framework import serializers

from apps.account.serializers.profile import UserInfoSerializer
from apps.bot.models import TelegramAuthRequest, TelegramAuthStatus


class TelegramAuthStartSerializer(serializers.ModelSerializer):
    """What the app needs to send someone to the bot: the link and the code to pick there."""
    request_id = serializers.UUIDField(source="id", read_only=True)
    deep_link = serializers.SerializerMethodField()

    class Meta:
        model = TelegramAuthRequest
        fields = ["request_id", "deep_link", "code", "expires_at"]
        read_only_fields = fields

    def get_deep_link(self, obj):
        # Passed in by the view: the link carries the start token, which
        # the row only keeps hashed, so it can't be rebuilt from obj.
        return self.context["deep_link"]


class TelegramLoginStartSerializer(TelegramAuthStartSerializer):
    poll_secret = serializers.SerializerMethodField()

    class Meta(TelegramAuthStartSerializer.Meta):
        fields = [*TelegramAuthStartSerializer.Meta.fields, "poll_secret"]
        read_only_fields = fields

    def get_poll_secret(self, obj):
        # Passed in by the view: only this response ever carries it, the
        # row keeps a hash.
        return self.context["poll_secret"]


class TelegramLoginPollSerializer(serializers.Serializer):
    request_id = serializers.UUIDField()
    poll_secret = serializers.CharField(max_length=128)


# ---------------------------------------------------------------------------
# Swagger only. The service builds these payloads as dicts
# (telegram_auth.poll_login / link_status); these describe them for the docs
# and must follow any change there.
# ---------------------------------------------------------------------------

_STATUS_CHOICES = [*TelegramAuthStatus.values, "expired"]


class _SessionDocSerializer(serializers.Serializer):
    access_token = serializers.CharField()
    refresh_token = serializers.CharField()
    user = UserInfoSerializer()


class TelegramLoginPollResultSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=_STATUS_CHOICES)
    message = serializers.CharField(allow_null=True)
    session = _SessionDocSerializer(allow_null=True)


class TelegramLinkStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[s for s in _STATUS_CHOICES if s != TelegramAuthStatus.USED],
    )
    message = serializers.CharField(allow_null=True)
    moved_subscriptions = serializers.IntegerField()
    user = UserInfoSerializer()
