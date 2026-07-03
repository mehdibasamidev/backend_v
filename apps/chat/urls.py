from django.urls import path
from .views import ChatHistoryView, ChatListView, CreateGlobalRoomView, GlobalRoomView, MarkAsReadView, StartChatView

urlpatterns = [
    # open chat with username
    path('chat/open/', StartChatView.as_view(), name='open-chat'),
    path('chat/rooms/', ChatListView.as_view(), name='chat-rooms'),
    # room_name can be "global" or a private ID like "user1_user2"
    path('chat/history/<uuid:room_id>/', ChatHistoryView.as_view(), name='chat-history'),
    path('chat/global/create/', CreateGlobalRoomView.as_view(), name='create-global-room'),
    path('chat/global/', GlobalRoomView.as_view(), name='global-room'),

    path('chat/read/', MarkAsReadView.as_view(), name='mark-as-read'),

]
