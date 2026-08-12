from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from drf_yasg.utils import swagger_auto_schema
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from apps.account.models import OtpChannel, OtpPurpose
from apps.account.serializers.auth import (
    EmailRegisterSerializer,
    EmailVerifySerializer,
    PhoneStartSerializer,
    PhoneVerifySerializer,
)
from apps.account.serializers.profile import UserInfoSerializer
from apps.account.services.otp import send_otp, verify_otp
from apps.account.services.registration import (
    create_email_user,
    create_phone_user,
    email_otp_required,
)
from apps.account.services.session import issue_session
from config.utils.response import SuccessResponse, BadRequestResponse

User = get_user_model()


def _auth_payload(user):
    return {**issue_session(user), "user": UserInfoSerializer(user).data}


class PhoneRegisterStartView(APIView):
    """
    Step 1: send a code to the number.

    Also used when the number already exists - the response says which it
    was, so the app can show "signing in" rather than "creating account"
    without the client having to probe for existence first (which would be
    an account-enumeration endpoint).
    """
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(request_body=PhoneStartSerializer)
    def post(self, request):
        serializer = PhoneStartSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        phone = serializer.validated_data["phone_number"]
        existing = User.objects.filter(phone_number=phone).first()

        purpose = (
            OtpPurpose.LOGIN_PHONE if existing else OtpPurpose.REGISTER_PHONE
        )
        send_otp(
            target=phone,
            channel=OtpChannel.SMS,
            purpose=purpose,
            user=existing,
        )

        return SuccessResponse(
            data={"phone_number": phone, "is_existing_user": bool(existing)},
            message="Verification code sent.",
        )


class PhoneVerifyView(APIView):
    """
    Step 2: confirm the code, then either sign in or create the account.
    Either way the caller gets a session back.
    """
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(request_body=PhoneVerifySerializer)
    def post(self, request):
        serializer = PhoneVerifySerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        phone = serializer.validated_data["phone_number"]
        code = serializer.validated_data["code"]

        existing = User.objects.filter(phone_number=phone).first()
        purpose = OtpPurpose.LOGIN_PHONE if existing else OtpPurpose.REGISTER_PHONE

        otp = verify_otp(target=phone, code=code, purpose=purpose)

        user = otp.user or existing
        if user is None:
            user = create_phone_user(phone)
        elif not user.is_phone_verified:
            # Covers a number attached before this flow existed.
            user.is_phone_verified = True
            user.save(update_fields=["is_phone_verified"])

        return SuccessResponse(
            data=_auth_payload(user),
            message="Signed in successfully.",
        )


class EmailRegisterView(APIView):
    """
    Email signup. Behaviour depends on the admin switch:

      OTP off - the account is created immediately, unverified.
      OTP on  - nothing is written yet; the password hash rides along on
                the OTP row and the account is created only once the code
                is confirmed. An abandoned signup must not leave the email
                address taken.
    """
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(request_body=EmailRegisterSerializer)
    def post(self, request):
        serializer = EmailRegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        email = serializer.validated_data["email"]
        password = serializer.validated_data["password"]

        if not email_otp_required():
            user = create_email_user(email, raw_password=password, verified=False)
            return SuccessResponse(
                data=_auth_payload(user),
                message="Account created.",
            )

        send_otp(
            target=email,
            channel=OtpChannel.EMAIL,
            purpose=OtpPurpose.REGISTER_EMAIL,
            payload={"password": make_password(password)},
        )
        return SuccessResponse(
            data={"email": email, "verification_required": True},
            message="Verification code sent to your email.",
        )


class EmailVerifyView(APIView):
    """Confirms an email signup code and creates the (verified) account."""
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(request_body=EmailVerifySerializer)
    def post(self, request):
        serializer = EmailVerifySerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        email = serializer.validated_data["email"].strip().lower()
        otp = verify_otp(
            target=email,
            code=serializer.validated_data["code"],
            purpose=OtpPurpose.REGISTER_EMAIL,
        )

        # Guard against two signups racing for the same address between the
        # code being sent and confirmed.
        if User.objects.filter(email__iexact=email).exists():
            return BadRequestResponse(message="This email is already registered.")

        user = create_email_user(
            email,
            hashed_password=otp.payload.get("password"),
            verified=True,
        )
        return SuccessResponse(
            data=_auth_payload(user),
            message="Account created and email verified.",
        )
