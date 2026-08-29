from rest_framework import serializers

from apps.referral.models import Referral, ReferralCode, ReferralSettings


class ReferralSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReferralSettings
        fields = [
            "required_for_signup",
            "personal_codes_enabled",
            "default_personal_max_uses",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]


class PublicReferralSettingsSerializer(serializers.ModelSerializer):
    """
    What the signup screen needs: whether to show the field and whether to
    mark it required. Deliberately does not expose use limits.
    """
    class Meta:
        model = ReferralSettings
        fields = ["required_for_signup"]


class ReferralCodeSerializer(serializers.ModelSerializer):
    remaining_uses = serializers.IntegerField(read_only=True, allow_null=True)
    is_usable = serializers.BooleanField(read_only=True)

    class Meta:
        model = ReferralCode
        fields = [
            "id", "code", "kind", "max_uses", "used_count",
            "remaining_uses", "is_usable", "is_active",
            "expires_at", "note", "created_at",
        ]
        read_only_fields = ["id", "code", "used_count", "created_at"]


class AdminReferralCodeCreateSerializer(serializers.Serializer):
    max_uses = serializers.IntegerField(min_value=0, default=0)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, max_length=200)

    # Lets an admin mint a specific, memorable code for a campaign instead
    # of a random one.
    code = serializers.CharField(required=False, allow_blank=True, max_length=32)


class AdminReferralSerializer(serializers.ModelSerializer):
    """
    The full picture for the admin: who invited whom, with what, and when.

    Enough on its own to answer "where did this account come from" without
    three more lookups - which is the whole reason the table exists ahead of
    the reward system.
    """
    code_value = serializers.CharField(source="code.code", read_only=True)
    code_kind = serializers.CharField(source="code.kind", read_only=True)

    referrer = serializers.SerializerMethodField()
    referred_user = serializers.SerializerMethodField()

    class Meta:
        model = Referral
        fields = [
            "id", "code_value", "code_kind",
            "referrer", "referred_user",
            "is_rewarded", "rewarded_at", "reward_note",
            "created_at",
        ]

    def _brief(self, user):
        if user is None:
            return None
        return {
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "phone_number": user.phone_number,
            "date_joined": user.date_joined,
        }

    def get_referrer(self, obj):
        # Null for admin-issued codes - there is nobody to credit.
        return self._brief(obj.referrer)

    def get_referred_user(self, obj):
        return self._brief(obj.referred_user)


class ReferralSerializer(serializers.ModelSerializer):
    referred_username = serializers.CharField(
        source="referred_user.username", read_only=True, default=None
    )
    code_value = serializers.CharField(source="code.code", read_only=True)

    class Meta:
        model = Referral
        fields = [
            "id", "code_value", "referred_username",
            "is_rewarded", "rewarded_at", "created_at",
        ]
