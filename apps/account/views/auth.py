from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.db.models import Q
from drf_yasg.utils import swagger_auto_schema
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from apps.account.models import AuthSettings, OtpChannel, OtpPurpose
from apps.account.serializers.auth import (
    AddEmailSerializer,
    AddPhoneSerializer,
    AuthSettingsSerializer,
    EmailVerifySerializer,
    PasswordLoginSerializer,
    PhoneVerifySerializer,
    ResetPasswordConfirmSerializer,
    ResetPasswordStartSerializer,
    SetUsernameSerializer,
)
from apps.account.serializers.profile import UserInfoSerializer
from apps.account.services.otp import send_otp, verify_otp
from apps.account.services.phone import normalize_phone
from apps.account.services.session import issue_session
from config.utils.response import (
    SuccessResponse,
    BadRequestResponse,
    NotFoundResponse,
)

User = get_user_model()


class PasswordLoginView(APIView):
    """Email, phone or username - all three arrive in `identifier`."""
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(request_body=PasswordLoginSerializer)
    def post(self, request):
        serializer = PasswordLoginSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        user = serializer.validated_data["user"]
        return SuccessResponse(
            data={**issue_session(user), "user": UserInfoSerializer(user).data},
            message="Signed in successfully.",
        )


class AuthSettingsPublicView(APIView):
    """
    Lets the app know whether email signups need a code, so it can route to
    the confirmation screen instead of guessing from the response shape.
    """
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        return SuccessResponse(
            data=AuthSettingsSerializer(AuthSettings.get_solo()).data,
            message="Auth settings retrieved successfully.",
        )


class SetUsernameView(APIView):
    """
    Onboarding step. Username is null until this runs, and the app router
    keeps the user here until it isn't.
    """
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(request_body=SetUsernameSerializer)
    def post(self, request):
        serializer = SetUsernameSerializer(
            data=request.data, context={"request": request}
        )
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        request.user.username = serializer.validated_data["username"]
        request.user.save(update_fields=["username"])
        return SuccessResponse(
            data=UserInfoSerializer(request.user).data,
            message="Username set successfully.",
        )


# ------------------------------------------------------- attach identifiers

class AddEmailStartView(APIView):
    """
    Attaches an email to a phone-only account.

    When the admin switch is off the address is stored straight away
    (unverified); when it's on a code is sent first. Either way the password
    is set here, because a phone-only account has none and an email login
    route without one would be unusable.
    """
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(request_body=AddEmailSerializer)
    def post(self, request):
        serializer = AddEmailSerializer(
            data=request.data, context={"request": request}
        )
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        email = serializer.validated_data["email"]
        password = serializer.validated_data.get("password")
        user = request.user

        if not AuthSettings.get_solo().email_otp_required:
            user.email = email
            user.is_email_verified = False
            if password:
                user.set_password(password)
            user.save()
            return SuccessResponse(
                data=UserInfoSerializer(user).data,
                message="Email added.",
            )

        send_otp(
            target=email,
            channel=OtpChannel.EMAIL,
            purpose=OtpPurpose.ADD_EMAIL,
            user=user,
            # Hashed before it is stored: the OTP row is short-lived but
            # still a database row, and a plaintext password in it would
            # be readable by anyone who could read the table.
            payload={"password": make_password(password)} if password else {},
        )
        return SuccessResponse(
            data={"email": email, "verification_required": True},
            message="Verification code sent to your email.",
        )


class AddEmailVerifyView(APIView):
    permission_classes = [IsAuthenticated]
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
            purpose=OtpPurpose.ADD_EMAIL,
        )

        # The code is bound to the account that requested it - otherwise a
        # code mailed to one user could be replayed by another to claim the
        # same address.
        if otp.user_id != request.user.id:
            return BadRequestResponse(message="This code is invalid or has expired.")

        if User.objects.filter(email__iexact=email).exists():
            return BadRequestResponse(message="This email is already in use.")

        user = request.user
        user.email = email
        user.is_email_verified = True
        if otp.payload.get("password"):
            # Already hashed at send time - assigning directly, because
            # set_password would hash the hash.
            user.password = otp.payload["password"]
        user.save()

        return SuccessResponse(
            data=UserInfoSerializer(user).data,
            message="Email verified and added.",
        )


class AddPhoneStartView(APIView):
    """
    Attaches a phone number to an existing account.

    Always requires a code, regardless of the email switch: an unverified
    number would be a sign-in route to this account that its real owner
    never consented to.
    """
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(request_body=AddPhoneSerializer)
    def post(self, request):
        serializer = AddPhoneSerializer(
            data=request.data, context={"request": request}
        )
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        phone = serializer.validated_data["phone_number"]
        send_otp(
            target=phone,
            channel=OtpChannel.SMS,
            purpose=OtpPurpose.ADD_PHONE,
            user=request.user,
        )
        return SuccessResponse(
            data={"phone_number": phone},
            message="Verification code sent.",
        )


class AddPhoneVerifyView(APIView):
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(request_body=PhoneVerifySerializer)
    def post(self, request):
        serializer = PhoneVerifySerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        phone = serializer.validated_data["phone_number"]
        otp = verify_otp(
            target=phone,
            code=serializer.validated_data["code"],
            purpose=OtpPurpose.ADD_PHONE,
        )
        if otp.user_id != request.user.id:
            return BadRequestResponse(message="This code is invalid or has expired.")

        if User.objects.filter(phone_number=phone).exists():
            return BadRequestResponse(message="This number is already in use.")

        user = request.user
        user.phone_number = phone
        user.is_phone_verified = True
        user.save(update_fields=["phone_number", "is_phone_verified"])

        return SuccessResponse(
            data=UserInfoSerializer(user).data,
            message="Phone number verified and added.",
        )


# ------------------------------------------------------------ password reset

class ResetPasswordStartView(APIView):
    """
    Sends a reset code to whichever channel the account can actually
    receive on.

    The response never reveals whether the account exists - a reset
    endpoint that says "no such user" is a free way to enumerate customers.
    """
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(request_body=ResetPasswordStartSerializer)
    def post(self, request):
        serializer = ResetPasswordStartSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        raw = serializer.validated_data["identifier"].strip()
        generic = SuccessResponse(
            message="If that account exists, a reset code has been sent."
        )

        lookup = Q(email__iexact=raw) | Q(username__iexact=raw)
        try:
            lookup |= Q(phone_number=normalize_phone(raw))
        except Exception:
            pass

        user = User.objects.filter(lookup).first()
        if user is None:
            return generic

        # Prefer the phone: it's immediate, and a verified number is
        # stronger proof of ownership than an unverified inbox.
        if user.phone_number and user.is_phone_verified:
            target, channel = user.phone_number, OtpChannel.SMS
        elif user.email:
            target, channel = user.email, OtpChannel.EMAIL
        else:
            return generic

        try:
            send_otp(
                target=target,
                channel=channel,
                purpose=OtpPurpose.RESET_PASSWORD,
                user=user,
            )
        except Exception:
            # Even a throttle rejection stays generic here, for the same
            # enumeration reason.
            return generic

        return generic


class ResetPasswordConfirmView(APIView):
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    @swagger_auto_schema(request_body=ResetPasswordConfirmSerializer)
    def post(self, request):
        serializer = ResetPasswordConfirmSerializer(data=request.data)
        if not serializer.is_valid():
            return BadRequestResponse(errors=serializer.errors)

        raw = serializer.validated_data["identifier"].strip()
        try:
            target = normalize_phone(raw)
        except Exception:
            target = raw.lower()

        otp = verify_otp(
            target=target,
            code=serializer.validated_data["code"],
            purpose=OtpPurpose.RESET_PASSWORD,
        )
        if otp.user is None:
            return NotFoundResponse(message="Account not found.")

        user = otp.user
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])

        return SuccessResponse(
            data={**issue_session(user), "user": UserInfoSerializer(user).data},
            message="Password updated.",
        )
