
from django.contrib.auth import login
from django.forms import ValidationError
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.parsers import JSONParser, FormParser
from rest_framework.renderers import JSONRenderer
from rest_framework_simplejwt.tokens import RefreshToken
from drf_yasg.utils import swagger_auto_schema
from django.core.files.temp import NamedTemporaryFile
from django.core.files import File
import requests

from config.utils.otp_handler import generate_and_store_otp
from apps.account.serializers import (
    RegisterSerializer,
    LoginSerializer,
    UserInfoSerializer,
    GoogleSignInSerializer,
    RegisteredUserResponseSerializer
)
from apps.account.serializers.auth import CheckEmailSerializer
from config.utils.response import (
    SuccessResponse,
    SuccessResponse201,
    BadRequestResponse,
    ServerErrorResponse,
)
from config.utils.custom_serializers import create_response_serializer
from apps.account.models import User
from django.core.cache import cache
from apps.account.serializers.auth import VerifyOtpSerializer


class RegisterView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="Step 2: Validate data and send OTP",
        request_body=RegisterSerializer,
        responses={200: "OTP Sent Successfully"}
    )
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            email = serializer.validated_data['email']

            # 1. Check if user already exists (Safety check)
            if User.objects.filter(email__iexact=email).exists():
                return BadRequestResponse(message="User already exists", code="EMAIL_TAKEN")

            # 2. Store validated data in cache & send OTP
            # Note: generate_and_store_otp should handle the send_mail logic
            try:
                generate_and_store_otp(email, serializer.validated_data)
                return SuccessResponse(
                    message="OTP sent to your email. Please verify to complete registration.",
                    code="OTP_SENT"
                )

            except ValidationError as ve:
                return BadRequestResponse(code="OTP_RATE_LIMIT", errors=ve)
            except Exception as e:
                return ServerErrorResponse(message="Failed to send OTP", errors=str(e), code="OTP_SEND_FAILURE")

        return BadRequestResponse(errors=serializer.errors)


class VerifyOtpView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="Step 3: Verify OTP and create account",
        request_body=VerifyOtpSerializer,
        responses={201: RegisteredUserResponseSerializer}
    )
    def post(self, request):
        serializer = VerifyOtpSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        email = serializer.validated_data['email']
        otp_received = serializer.validated_data['otp']

        # 1. Retrieve pending data from cache
        cached_data = cache.get(f"pending_reg_{email}")
        cached_otp = cached_data['otp']
        cached_password = cached_data['password']

        if not cached_data or cached_otp != otp_received:
            return BadRequestResponse(message="Invalid or expired OTP", code="INVALID_OTP")

        # 2. OTP is correct. Create the user using the RegisterSerializer logic
        # We pass the data we saved in Step 2 back into the serializer
        reg_data = {'email': email, 'password': cached_password}
        register_serializer = RegisterSerializer(data=reg_data)
        if register_serializer.is_valid():
            user = register_serializer.save()

            # 3. Clean up cache and generate tokens
            cache.delete(f"pending_reg_{email}")
            refresh = RefreshToken.for_user(user)

            response_data = {
                "access_token": str(refresh.access_token),
                "refresh_token": str(refresh),
                "status": "success",
                "user": user
            }

            return SuccessResponse201(
                message="Account verified and created successfully!",
                data=RegisteredUserResponseSerializer(response_data).data
            )

        return BadRequestResponse(errors=register_serializer.errors)


class LoginView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="Login Panel",
        request_body=LoginSerializer,
        responses={
            200: create_response_serializer(
                data_serializer_class=RegisteredUserResponseSerializer,
                text_message="Login successful"
            )
        },
    )
    def post(self, request):
        try:
            serializer = LoginSerializer(data=request.data)
            if serializer.is_valid():
                user = serializer.validated_data
                refresh = RefreshToken.for_user(user)
                login(request, user)
                response = {
                    "access_token": str(refresh.access_token),
                    "refresh_token": str(refresh),
                    "status": "success",
                    "user": user  # pass user instance directly
                    }
                # Serialize using RegisteredUserResponseSerializer
                response_serializer = RegisteredUserResponseSerializer(response)
                return SuccessResponse(
                    message="Login successful",
                    data=response_serializer.data,
                )
            return BadRequestResponse(errors=serializer.errors)
        except Exception as e:
            return ServerErrorResponse(errors=str(e))


class GoogleSignInView(APIView):
    parser_classes = [FormParser, JSONParser]
    renderer_classes = [JSONRenderer]
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="Sign in or register with Google access token",
        request_body=GoogleSignInSerializer,
        responses={
            200: RegisteredUserResponseSerializer,
            201: RegisteredUserResponseSerializer,
            400: "Bad Request",
        },
    )
    def post(self, request):
        try:
            serializer = GoogleSignInSerializer(data=request.data)
            if not serializer.is_valid():
                return BadRequestResponse(
                    message="Invalid data",
                    errors=serializer.errors
                )

            access_token = serializer.validated_data["access_token"]
            if not access_token:
                return BadRequestResponse(message="Access token cannot be empty.")

            # Fetch user info from Google
            response = requests.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if response.status_code != 200:
                return BadRequestResponse(message="Invalid Google access token.")

            user_info = response.json()
            email = user_info.get("email")
            full_name = user_info.get("name", "")
            google_id = user_info.get("sub")
            picture_link = user_info.get("picture")

            if not email or not google_id:
                return BadRequestResponse(message="Failed to retrieve user info from Google.")

            # Get or create user
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "full_name": full_name,
                    "google_id": google_id,

                }
            )

            # New user setup
            if created:
                # Prevent password login until explicitly set
                user.set_unusable_password()
                user.save()

                # Save profile picture from Google
                if picture_link:
                    pic_response = requests.get(picture_link)
                    if pic_response.status_code == 200:
                        temp_file = NamedTemporaryFile(delete=True)
                        temp_file.write(pic_response.content)
                        temp_file.flush()
                        user.profile_picture.save(
                            f"{user.username}_google.png",
                            File(temp_file),
                            save=True,
                        )

            # Login user
            login(request, user)
            refresh = RefreshToken.for_user(user)

            data = {
                "access_token": str(refresh.access_token),
                "refresh_token": str(refresh),
                "status": "success",
                "user": UserInfoSerializer(user).data,
            }

            return SuccessResponse201(
                message="Google sign-in successful" if created else "Login successful",
                data=data
            )

        except Exception as e:
            return ServerErrorResponse(errors=str(e))


class CheckEmailView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description="Check if a user exists with the given email",
        request_body=CheckEmailSerializer,
        responses={
            200: "Returns { 'exists': true/false }",
        },
    )
    def post(self, request):
        serializer = CheckEmailSerializer(data=request.data)
        if serializer.is_valid():
            email = serializer.validated_data['email']
            exists = User.objects.filter(email__iexact=email).exists()
            if exists:
                return SuccessResponse(
                    data={"exists": True},
                    message="Email is already registered",
                    code="EMAIL_TAKEN"
                )

            return SuccessResponse(
                data={"exists": False},
                message="Email is available",
                code="EMAIL_AVAILABLE"
            )
        return BadRequestResponse(errors=serializer.errors)


class RegisterVpnView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description=" create account",
        request_body=RegisterSerializer,
        responses={201: RegisteredUserResponseSerializer}
    )
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        email = serializer.validated_data['email']
        password = serializer.validated_data['password']
        # otp_received = serializer.validated_data['otp']

        # 1. Retrieve pending data from cache
        # cached_data = cache.get(f"pending_reg_{email}")
        # cached_otp = cached_data['otp']
        # cached_password = cached_data['password']

        # if not cached_data or cached_otp != otp_received:
        #     return BadRequestResponse(message="Invalid or expired OTP", code="INVALID_OTP")

        # 2. OTP is correct. Create the user using the RegisterSerializer logic
        # We pass the data we saved in Step 2 back into the serializer
        reg_data = {'email': email, 'password': password}
        register_serializer = RegisterSerializer(data=reg_data)
        if register_serializer.is_valid():
            user = register_serializer.save()

            # 3. Clean up cache and generate tokens
            cache.delete(f"pending_reg_{email}")
            refresh = RefreshToken.for_user(user)

            response_data = {
                "access_token": str(refresh.access_token),
                "refresh_token": str(refresh),
                "status": "success",
                "user": user
            }

            return SuccessResponse201(
                message="Account verified and created successfully!",
                data=RegisteredUserResponseSerializer(response_data).data
            )

        return BadRequestResponse(errors=register_serializer.errors)
