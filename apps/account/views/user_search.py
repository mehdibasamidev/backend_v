from django.contrib.auth import get_user_model
from django.db.models import Q
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from apps.account.serializers.user_search import UserSearchSerializer
from apps.chat.presence import get_online_users
from config.utils.custom_serializers import create_response_serializer
from config.utils.response import (
    SuccessResponse,
    BadRequestResponse,
    ServerErrorResponse,
)

User = get_user_model()


class UserSearchView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]

    @swagger_auto_schema(
        operation_description="Search users by username",
        manual_parameters=[
            openapi.Parameter(
                "q",
                openapi.IN_QUERY,
                description="search query for username",
                type=openapi.TYPE_STRING,
            )
        ],
        responses={
            200: create_response_serializer(
                data_serializer_class=None,
                text_message="Users retrieved successfully",
            )
        },
    )
    def get(self, request):
        try:
            query = request.GET.get("q", "").strip()
            if not query:
                return BadRequestResponse(message="Query parameter 'q' is required")

            users = (
                User.objects
                # Username only. Searching email here let anyone confirm
                # whether a given address has an account, and now that a
                # phone number is also a sign-in route the same search would
                # expose those too - a directory of customer contact details
                # is not what a "find someone to chat with" box should be.
                .filter(Q(username__icontains=query))
                .exclude(id=request.user.id)
                # Accounts still in onboarding have no username to match or
                # display, so they should not surface at all.
                .exclude(username__isnull=True)[:10]
            )

            try:
                online_users = get_online_users()
            except Exception:
                online_users = []

            data = UserSearchSerializer(users, many=True).data
            for user in data:
                user["is_online"] = str(user["id"]) in online_users

            return SuccessResponse(
                data=data, message="Users retrieved successfully"
            )
        except Exception as e:
            return ServerErrorResponse(errors=str(e))
