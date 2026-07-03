import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.db.models import F
from .models import Room, Message, MessageRead, UserRoomStatus

# ✅ presence helpers
from apps.chat.presence import add_online_user, get_online_users, remove_online_user
from django.utils import timezone
from django.contrib.auth import get_user_model
User = get_user_model()


class ChatConsumer(AsyncWebsocketConsumer):

    # ---------------------------
    # CONNECT
    # ---------------------------
    async def connect(self):
        self.user = self.scope["user"]

        if not self.user.is_authenticated:
            await self.close()
            return

        self.room_id = self.scope["url_route"]["kwargs"].get("room_id")
        # detect global connection
        self.is_global_only = self.room_id == "global"

        # ✅ mark user ONLINE
        await database_sync_to_async(add_online_user)(self.user.id)

        # ---------------------------
        # GLOBAL GROUP AlWAYS JOIN
        # ---------------------------
        self.global_group = "chat_global"
        await self.channel_layer.group_add(self.global_group, self.channel_name)

        # ---------------------------
        # ROOM GROUP (only if not global)
        # ---------------------------
        self.room_group = None
        if self.room_id and not self.is_global_only:
            # ✅ check room permission
            allowed = await self.is_room_member(self.user.id, self.room_id)
            if not allowed:
                await self.close()
                return
            self.room_group = f"chat_{self.room_id}"
            await self.channel_layer.group_add(self.room_group, self.channel_name)
            # mark as read + emit seen
            last_message = await self.mark_room_as_read(self.user.id, self.room_id)
            if last_message:
                await self.emit_seen_event(self.room_id, last_message.id)

        # ✅ accept connection
        await self.accept()
        # ✅ broadcast ONLINE status
        await self.channel_layer.group_send(
            self.global_group,
            {
                "type": "user_status",
                "user_id": str(self.user.id),
                "status": "online"
            }
        )
        # ✅ send current online users to THIS user only

        online_users = await database_sync_to_async(get_online_users)()

        await self.send(text_data=json.dumps({

            "type": "initial_online_users",

            "users": [str(user_id) for user_id in online_users]

        }))

    # ---------------------------
    # DISCONNECT
    # ---------------------------
    async def disconnect(self, close_code):

        if hasattr(self, "user") and self.user.is_authenticated:

            # ✅ ONLY mark offline if GLOBAL socket is closed
            if getattr(self, "is_global_only", False):

                await database_sync_to_async(remove_online_user)(self.user.id)
                await self.update_last_seen(self.user.id)

                await self.channel_layer.group_send(
                    "chat_global",
                    {
                        "type": "user_status",
                        "user_id": str(self.user.id),
                        "status": "offline",
                        "last_seen": timezone.now().isoformat()
                    }
                )

        # ✅ Always clean up groups (VERY IMPORTANT)
        if hasattr(self, "global_group"):
            await self.channel_layer.group_discard(
                self.global_group,
                self.channel_name
            )

        if hasattr(self, "room_group") and self.room_group:
            await self.channel_layer.group_discard(
                self.room_group,
                self.channel_name
            )

    # ---------------------------
    # RECEIVE MESSAGE
    # ---------------------------
    async def receive(self, text_data):
        data = json.loads(text_data)

        message_type = data.get("type", "message")
        room_id = data.get("room_id")
        user_id_str = str(self.user.id)

        # ---------------------------
        # 🔵 SEEN EVENT (from client)
        # ---------------------------
        if message_type == "seen":
            if not room_id or room_id == "global":
                return

            last_message = await self.mark_room_as_read(self.user.id, room_id)

            if last_message:
                await self.emit_seen_event(room_id, last_message.id)

            return

        # ---------------------------
        # 🔵 SEND MESSAGE
        # ---------------------------
        if message_type == "message":

            message_text = data.get("content")

            if not message_text or not room_id:
                return

            # ---------------------------
            # GLOBAL ROOM (admin only)
            # ---------------------------
            if await self.is_global_room(room_id):

                if not await self.is_admin(self.user.id):
                    return

                room = await self.get_global_room()

                message = await self.create_message(self.user.id, room.id, message_text)

                event_payload = {
                    "type": "chat_message",
                    "content": message.content,
                    "username": self.user.username,
                    "room_id": str(room.id),
                    "message_id": str(message.id),
                    "user_id": user_id_str
                }

                # send to global group
                await self.channel_layer.group_send(self.global_group, event_payload)

                return

            # ---------------------------
            # NORMAL ROOM
            # ---------------------------
            message = await self.create_message(self.user.id, room_id, message_text)

            is_me = str(self.user.id) == user_id_str

            event_payload = {
                "type": "chat_message",
                "content": message.content,
                "username": self.user.username,
                "room_id": str(room_id),
                "message_id": str(message.id),
                "user_id": user_id_str,
                "is_me": is_me
            }

            # ---------------------------
            # send to ROOM (chat screen)
            # ---------------------------
            if room_id and room_id != "global":
                await self.channel_layer.group_send(
                    f"chat_{room_id}",
                    event_payload
                )

            # ---------------------------
            # 🔥 send to GLOBAL (inbox update)
            # ---------------------------
            await self.channel_layer.group_send(
                self.global_group,
                event_payload
            )

            return

    # ---------------------------
    # MESSAGE EVENT
    # ---------------------------
    async def chat_message(self, event):
        await self.send(text_data=json.dumps({
            **event,
            "type": "message"
        }))

        # send delivered
        if not event.get("is_me"):
            await self.emit_delivered_event(event)

    # ---------------------------
    # SEEN EVENT
    # ---------------------------
    async def seen_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "seen",
            "room_id": event["room_id"],
            "user_id": event["user_id"],
            "message_id": event["message_id"]
        }))

    async def emit_seen_event(self, room_id, message_id):
        await self.channel_layer.group_send(
            f"chat_{room_id}",
            {
                "type": "seen_event",
                "room_id": room_id,
                "user_id": str(self.user.id),
                "message_id": str(message_id)
            }
        )

    # ---------------------------
    # DELIVERED EVENT
    # ---------------------------
    async def emit_delivered_event(self, event):
        await self.channel_layer.group_send(
            f"chat_{event['room_id']}",
            {
                "type": "delivered_event",
                "room_id": event["room_id"],
                "message_id": event["message_id"],
                "user_id": str(self.user.id)
            }
        )

    async def delivered_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "delivered",
            "room_id": event["room_id"],
            "message_id": event["message_id"],
            "user_id": event["user_id"]
        }))

    # ---------------------------
    # 🔥 USER ONLINE/OFFLINE EVENT
    # ---------------------------
    async def user_status(self, event):
        await self.send(text_data=json.dumps({
            "type": "status",
            "user_id": event["user_id"],
            "status": event["status"],
            "last_seen": event.get("last_seen")
        }))

    # ===========================
    # DATABASE METHODS
    # ===========================
    @database_sync_to_async
    def update_last_seen(self, user_id):  # <--- Added 'self' here
        User.objects.filter(id=user_id).update(last_seen=timezone.now())

    @database_sync_to_async
    def is_room_member(self, user_id, room_id):
        return Room.objects.filter(
            id=room_id,
            participants__id=user_id
        ).exists()

    @database_sync_to_async
    def get_global_room(self):
        return Room.objects.get(is_global=True)

    @database_sync_to_async
    def is_global_room(self, room_id):
        return Room.objects.filter(id=room_id, is_global=True).exists()

    @database_sync_to_async
    def create_message(self, user_id, room_id, content):

        room = Room.objects.get(id=room_id)

        message = Message.objects.create(
            sender=self.user,
            room=room,
            content=content
        )

        # update timestamp
        room.save(update_fields=["updated_at"])

        # ensure status
        for user in room.participants.all():
            UserRoomStatus.objects.get_or_create(user=user, room=room)

        # increment unread
        UserRoomStatus.objects.filter(room=room).exclude(user_id=user_id).update(
            unread_count=F('unread_count') + 1
        )

        return message

    @database_sync_to_async
    def mark_room_as_read(self, user_id, room_id):
        room = Room.objects.get(id=room_id)

        messages = room.messages.exclude(sender_id=user_id)
        last_message = room.messages.last()

        for msg in messages:
            MessageRead.objects.get_or_create(
                message=msg,
                user_id=user_id
            )

        if last_message:
            UserRoomStatus.objects.update_or_create(
                user_id=user_id,
                room=room,
                defaults={
                    "last_read_message": last_message,
                    "unread_count": 0
                }
            )

        return last_message

    @database_sync_to_async
    def mark_messages_as_read(self, user_id, message_ids):
        messages = Message.objects.filter(id__in=message_ids).exclude(sender_id=user_id)

        for msg in messages:
            MessageRead.objects.get_or_create(
                message=msg,
                user_id=user_id
            )

    @database_sync_to_async
    def is_admin(self, user_id):
        return (
            self.user.is_staff or
            self.user.groups.filter(name="admin").exists()
        )
