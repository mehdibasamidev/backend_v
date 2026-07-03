from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from drf_yasg.utils import swagger_auto_schema

from apps.chat.models import Message, MessageRead, Room, UserRoomStatus
from apps.account.models import User
from apps.chat.serializers import MessageSerializer, SearchUserSerializers
from apps.account.serializers.profile import UserPresenceSerializer
from config.utils.response import (
    SuccessResponse,
    BadRequestResponse,
    ServerErrorResponse,
)


class ChatHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, room_id):
        try:
            room = get_object_or_404(Room, id=room_id)

            # ✅ SECURITY CHECK
            if not room.participants.filter(id=request.user.id).exists():
                return BadRequestResponse(errors="Access denied")

            messages = Message.objects.filter(
                room=room
            ).select_related("sender").order_by("-timestamp")

            serializer = MessageSerializer(messages, many=True, context={"user": request.user})

            return SuccessResponse(
                data=serializer.data,
                message="History retrieved"
            )

        except Exception as e:
            return ServerErrorResponse(errors=str(e))


class ChatListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            statuses = UserRoomStatus.objects.select_related(
                "room",
                "last_read_message"
            ).filter(user=request.user)

            rooms = []

            for status in statuses:
                room = status.room

                last_msg = room.messages.order_by("-timestamp").first()

                if room.is_group:
                    name = room.name
                else:
                    other = room.participants.exclude(id=request.user.id).first()

                    other_user_data = UserPresenceSerializer(other).data if other else None
                    name = other.username if other else "Unknown"

                rooms.append({
                    "id": str(room.id),
                    "room_id": str(room.id),
                    "room_name": name,
                    "last_message": last_msg.content if last_msg else "",
                    "timestamp": last_msg.timestamp if last_msg else room.updated_at,
                    "unread_count": status.unread_count,
                    "is_global": room.is_global,
                    "pinned": room.is_global,
                    "other_user": other_user_data,
                    "last_seen": other.last_seen if other else None,
                })

            # ✅ GLOBAL ROOM
            # global_room = Room.objects.filter(is_global=True).first()

            # if global_room:
            #     last_msg = global_room.messages.order_by("-timestamp").first()

            #     rooms.insert(0, {
            #         "room_id": str(global_room.id),
            #         "room_name": "Global",
            #         "last_message": last_msg.content if last_msg else "Welcome to Global Chat",
            #         "timestamp": last_msg.timestamp if last_msg else global_room.updated_at,
            #         "unread_count": 0,
            #         "is_global": True,
            #         "pinned": True,
            #     })

            # Sort the final list by timestamp (newest first)
            rooms.sort(key=lambda x: x['timestamp'], reverse=True)
            # If you want pinned/global rooms at the very top regardless of time:
            # rooms.sort(key=lambda x: (not x.get('pinned', False), x['timestamp']), reverse=True)
            return SuccessResponse(data=rooms)

        except Exception as e:
            return ServerErrorResponse(errors=str(e))


class StartChatView(APIView):
    permission_classes = [IsAuthenticated]

    # swagger to get user id
    @swagger_auto_schema(
        operation_description="Step 3: Verify OTP and create account",
        request_body=SearchUserSerializers,
    )
    def post(self, request):
        try:
            user = request.user
            target_id = request.data.get("user_id")

            if not target_id:
                return BadRequestResponse(errors="user_id required")

            target = get_object_or_404(User, id=target_id)

            if target == user:
                return BadRequestResponse(errors="Cannot chat with yourself")

            # ✅ find existing DM
            room = Room.objects.filter(
                is_group=False,
                participants=user
            ).filter(
                participants=target
            ).first()

            room_name = target.username
            # ✅ create if not exists
            if not room:
                room = Room.objects.create(is_group=False)

                room.participants.add(user, target)

                UserRoomStatus.objects.get_or_create(user=user, room=room)
                UserRoomStatus.objects.get_or_create(user=target, room=room)

            return SuccessResponse(
                data={"room_id": str(room.id), "room": {
                        "id": str(room.id),
                        "room_id": str(room.id),
                        "room_name": room_name,
                        "other_user": UserPresenceSerializer(target).data,
                }},
                message="Chat ready"
            )

        except Exception as e:
            return ServerErrorResponse(errors=str(e))


class CreateGlobalRoomView(APIView):
    permission_classes = [IsAuthenticated]

    def is_admin(self, user):
        return user.is_staff or user.groups.filter(name="admin").exists()

    def post(self, request):
        try:
            if not self.is_admin(request.user):
                return BadRequestResponse(errors="Permission denied")

            room, created = Room.objects.get_or_create(
                is_global=True,
                defaults={
                    "name": "Global",
                    "is_group": True
                }
            )

            return SuccessResponse(
                data={
                    "room_id": str(room.id),
                    "created": created
                },
                message="Global room ready"
            )

        except Exception as e:
            return ServerErrorResponse(errors=str(e))


class MarkAsReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            room_id = request.data.get("room_id")

            if not room_id:
                return BadRequestResponse(errors="room_id required")

            room = get_object_or_404(Room, id=room_id)

            if not room.participants.filter(id=request.user.id).exists():
                return BadRequestResponse(errors="Access denied")

            messages = room.messages.exclude(sender=request.user)

            MessageRead.objects.bulk_create([
                MessageRead(message=msg, user=request.user)
                for msg in messages
            ], ignore_conflicts=True)

            last_msg = room.messages.order_by("-timestamp").first()

            UserRoomStatus.objects.update_or_create(
                user=request.user,
                room=room,
                defaults={
                    "last_read_message": last_msg,
                    "unread_count": 0
                }
            )

            return SuccessResponse(message="Marked as read")

        except Exception as e:
            return ServerErrorResponse(errors=str(e))


class GlobalRoomView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            room = get_object_or_404(Room, is_global=True)

            return SuccessResponse(
                data={
                    "room_id": str(room.id),
                    "name": "Global"
                }
            )

        except Exception as e:
            return ServerErrorResponse(errors=str(e))
