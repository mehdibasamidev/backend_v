from rest_framework import serializers
from django.contrib.auth import get_user_model

User = get_user_model()


class UserSearchSerializer(serializers.ModelSerializer):
    is_online = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "email", "last_seen", "is_online"]

    def get_is_online(self, obj):
        online_users = self.context.get("online_users", set())
        return str(obj.id) in online_users
