from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    # Captures the Room UUID: ws://127.0.0.1:8000/ws/chat/<uuid>/
    re_path(r'ws/chat/(?P<room_id>[^/]+)/$', consumers.ChatConsumer.as_asgi()),
    re_path(r'ws/chat/global/$', consumers.ChatConsumer.as_asgi()),
]
