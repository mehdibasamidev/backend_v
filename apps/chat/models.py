import uuid
from django.db import models
from django.conf import settings


class Room(models.Model):
    # Unique ID for the room (e.g., a UUID or a simple slug)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # For Group Chats, give it a name. For Private, leave it blank.
    name = models.CharField(max_length=255, null=True, blank=True)
    # Many-to-Many: This links Alice and Bob to this room
    participants = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="rooms")
    is_group = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)  # Great for sorting the chat list
    is_global = models.BooleanField(default=False,)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name if self.name else f"Room {self.id}"


class Message(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    # NEW: for quick unread optimization (optional)
    is_deleted = models.BooleanField(default=False)

    class Meta:

        ordering = ['timestamp']
        indexes = [
            models.Index(fields=['room', 'timestamp']),
        ]


class MessageRead(models.Model):
    message = models.ForeignKey("Message", on_delete=models.CASCADE, related_name="reads")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('message', 'user')


class UserRoomStatus(models.Model):

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    room = models.ForeignKey(Room, on_delete=models.CASCADE)

    last_read_message = models.ForeignKey(

        Message,

        on_delete=models.SET_NULL,

        null=True,

        blank=True

    )

    unread_count = models.IntegerField(default=0)

    class Meta:

        unique_together = ("user", "room")


class MessageDelivery(models.Model):

    message = models.ForeignKey(Message, on_delete=models.CASCADE)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    delivered_at = models.DateTimeField(null=True, blank=True)

    seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:

        unique_together = ("message", "user")
