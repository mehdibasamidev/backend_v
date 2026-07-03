from datetime import timedelta
from django.utils import timezone
from rest_framework import serializers
from apps.account.models import User


class UserInfoSerializer(serializers.ModelSerializer):
    rate = serializers.FloatField(read_only=True)

    class Meta:
        model = User
        exclude = [
            "password",
            "is_superuser",
            "is_staff",
            "groups",
            "user_permissions",
        ]


class UserProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        exclude = [
            "id",
            "email",
            # "username",
            "password",
            "last_login",
            "is_superuser",
            "is_staff",
            "is_active",
            "groups",
            "user_permissions",
            "date_joined",
        ]

    def validate_username(self, value):
        # Check if username is already taken by someone else
        if User.objects.filter(username__iexact=value).exclude(id=self.instance.id).exists():
            raise serializers.ValidationError("This username is already taken.")
        return value


class UserPresenceSerializer(serializers.ModelSerializer):

    is_online = serializers.SerializerMethodField()

    last_seen_display = serializers.SerializerMethodField()

    class Meta:

        model = User

        fields = ["id", "username", "is_online", "last_seen_display", "last_seen"]

    def get_is_online(self, obj):

        if not obj.last_seen:

            return False

        now = timezone.now()

        return now - obj.last_seen < timedelta(seconds=30)

    def get_last_seen_display(self, obj):
        if not obj.last_seen:
            return "never seen"

        now = timezone.now()
        diff = now - obj.last_seen

        if diff < timedelta(minutes=1):
            return "just now"
        elif diff < timedelta(hours=1):
            return f"{diff.seconds // 60} min ago"
        elif diff < timedelta(days=1):
            return f"{diff.seconds // 3600} hours ago"
        else:
            return obj.last_seen.strftime("%d %b")
