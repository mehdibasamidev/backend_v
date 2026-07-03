from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.chat"

    def ready(self):

        import apps.chat.signals  # noqa: F401
        from django.db.utils import OperationalError, ProgrammingError

        try:

            from .models import Room

            # Ensure only one global room exists

            global_room, _ = Room.objects.get_or_create(

                is_global=True,

                defaults={

                    "name": "Global Chat",

                    "is_group": True,

                }

            )

        except (OperationalError, ProgrammingError):

            # This happens during migrations or when DB is not ready

            pass
