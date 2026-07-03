from django.contrib.auth import get_user_model
from .models import Room

User = get_user_model()


def get_or_create_dm_room(user1, user2):
    sorted([user1.id, user2.id])
    room = Room.objects.filter(
        is_group=False,
        participants=user1
    ).filter(
        participants=user2
    ).first()

    if room:
        return room

    room = Room.objects.create(is_group=False)
    room.participants.add(user1, user2)
    return room
