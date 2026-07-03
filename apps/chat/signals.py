from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings

from .models import Room

User = settings.AUTH_USER_MODEL


@receiver(post_save, sender=User)
def add_user_to_global(sender, instance, created, **kwargs):
    if created:
        global_room = Room.objects.filter(is_global=True).first()

        if global_room:
            global_room.participants.add(instance)
