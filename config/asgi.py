import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
import apps.chat.routing
from config.middleware import TokenAuthMiddleware  # 👈 add this

application = ProtocolTypeRouter({
    "http": django_asgi_app,

    "websocket": TokenAuthMiddleware(   # 👈 replace AuthMiddlewareStack
        URLRouter(
            apps.chat.routing.websocket_urlpatterns
        )
    ),
})
