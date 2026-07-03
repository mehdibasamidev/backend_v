
from rest_framework import serializers
from .models import Message, MessageRead, MessageDelivery


class MessageSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='sender.username', read_only=True)
    user_id = serializers.UUIDField(source='sender.id', read_only=True)
    status = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ['id', 'user_id', 'username', 'content', 'timestamp', 'status']

    def get_status(self, obj):
        current_user = self.context.get("user")

        if not current_user:
            return "sent"

        # ❗ If message is NOT mine → always seen
        if obj.sender_id != current_user.id:
            return "seen"

        # 👇 Now we are sure: message is MINE

        # Get all other participants
        other_users = obj.room.participants.exclude(id=current_user.id)

        # ✅ SEEN: all other users read
        seen_count = MessageRead.objects.filter(
            message=obj,
            user__in=other_users
        ).count()

        if seen_count == other_users.count():
            return "seen"

        # ✅ DELIVERED
        delivered_count = MessageDelivery.objects.filter(
            message=obj,
            user__in=other_users
        ).count()

        if delivered_count > 0:
            return "delivered"

        return "sent"


class ChatListSerializer(serializers.Serializer):
    """
    We use a Serializer instead of ModelSerializer here because
    the Chat List is a custom combination of Room and Message data.
    """
    room_id = serializers.UUIDField()
    room_name = serializers.CharField()
    last_message = serializers.CharField()
    timestamp = serializers.DateTimeField()
    unread_count = serializers.IntegerField(default=0)  # Optional bonus


class SearchUserSerializers(serializers.Serializer):
    user_id = serializers.UUIDField()
