from .models import Room


def get_or_create_global_room():
    room, created = Room.objects.get_or_create(
        is_global=True,
        defaults={
            "name": "Global",
            "is_group": True,
        }
    )
    return room
