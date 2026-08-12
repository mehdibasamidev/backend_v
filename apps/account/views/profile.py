from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from drf_yasg.utils import swagger_auto_schema
from rest_framework.parsers import MultiPartParser, JSONParser
from rest_framework.renderers import JSONRenderer
from apps.account.serializers.profile import UserInfoSerializer, UserProfileUpdateSerializer
from config.utils.response import (
    SuccessResponse,
    BadRequestResponse,
    ServerErrorResponse,
)
from config.utils.custom_serializers import create_response_serializer


class UserProfileView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, JSONParser]
    renderer_classes = [JSONRenderer]

    @swagger_auto_schema(
        operation_description="Retrieve authenticated user's profile",
        responses={
            200: create_response_serializer(
                data_serializer_class=UserInfoSerializer,
                text_message="User profile retrieved successfully"
            )
        }
    )
    def get(self, request):
        return SuccessResponse(
            data=UserInfoSerializer(request.user).data,
            message="User profile retrieved successfully",
        )

    @swagger_auto_schema(request_body=UserProfileUpdateSerializer)
    def patch(self, request):
        try:
            serializer = UserProfileUpdateSerializer(
                request.user,
                data=request.data,
                partial=True,
            )
            if serializer.is_valid():
                serializer.save()
                return SuccessResponse(
                    message="Profile updated successfully",
                    data=UserInfoSerializer(request.user).data,
                )
            return BadRequestResponse(errors=serializer.errors)
        except Exception as e:
            return ServerErrorResponse(errors=str(e))
