from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from drf_yasg.utils import swagger_auto_schema
from rest_framework.parsers import JSONParser
from rest_framework.renderers import JSONRenderer
from django.contrib.auth import get_user_model
from django.db.models import Q
from drf_yasg import openapi

from apps.account.serializers.user_search import UserSearchSerializer
from config.utils.response import (
    SuccessResponse,
    BadRequestResponse,
    ServerErrorResponse,
)
from config.utils.custom_serializers import create_response_serializer
from apps.chat.presence import get_online_users
User = get_user_model()


class UserSearchView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]

    @swagger_auto_schema(
        operation_description="Search users by username or email",
        manual_parameters=[
            openapi.Parameter(
                'q',
                openapi.IN_QUERY,
                description="search query for username or email",
                type=openapi.TYPE_STRING,
            )
        ],  # optional: you can later document 'q'
        responses={
            200: create_response_serializer(
                data_serializer_class=None,  # optional: we return custom dict
                text_message="Users retrieved successfully"
            )
        }
    )
    def get(self, request):

        try:

            query = request.GET.get("q", "").strip()

            if not query:

                return BadRequestResponse(message="Query parameter 'q' is required")

            users = User.objects.filter(

                Q(username__icontains=query) |

                Q(email__icontains=query)

            ).exclude(id=request.user.id)[:10]

            online_users = []

            try:
                online_users = get_online_users()
            except Exception:
                online_users = []  # fallback safe

            serializer = UserSearchSerializer(users, many=True)

            data = serializer.data

            # add online flag

            for user in data:

                user["is_online"] = str(user["id"]) in online_users

            return SuccessResponse(

                data=data,

                message="Users retrieved successfully"

            )

        except Exception as e:

            return ServerErrorResponse(errors=str(e))
