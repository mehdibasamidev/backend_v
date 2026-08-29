from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.account.models import AuthSettings
from apps.account.services.phone import normalize_phone
from config.utils.exceptions import BadRequestException

User = get_user_model()


class PhoneField(serializers.CharField):
    """Every phone that enters the API is stored in one canonical form."""

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        try:
            return normalize_phone(value)
        except BadRequestException as exc:
            raise serializers.ValidationError(exc.message)


# ---------------------------------------------------------------- register

class PhoneStartSerializer(serializers.Serializer):
    """Step 1 of a phone signup or a phone sign-in - just the number."""
    phone_number = PhoneField()


class PhoneVerifySerializer(serializers.Serializer):
    phone_number = PhoneField()
    code = serializers.CharField(min_length=4, max_length=8)


class EmailRegisterSerializer(serializers.Serializer):
    """
    Step 1 of an email signup.

    Whether this creates the account outright or only sends a code depends
    on AuthSettings.email_otp_required, so the view decides - the
    serializer only guarantees the input is usable.
    """
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=4)

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("This email is already registered.")
        return value

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value


class EmailVerifySerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(min_length=4, max_length=8)


# ------------------------------------------------------------------- login

class PasswordLoginSerializer(serializers.Serializer):
    """
    One field for all three password routes - email, phone or username.
    Which one it is gets resolved by MultiIdentifierBackend, so the client
    doesn't have to guess or offer a picker.
    """
    identifier = serializers.CharField(required=False)
    email = serializers.EmailField(required=False)
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        identifier = (
            attrs.get("identifier")
            or attrs.get("email")
        )
        if not identifier:
            raise serializers.ValidationError(
                {"identifier": "Email or identifier is required."}
            )
        user = authenticate(
            username=identifier.strip(),
            password=attrs["password"],)
        if not user:
            raise serializers.ValidationError("Invalid credentials.")
        if not user.is_active:
            raise serializers.ValidationError(
                "This account is disabled.")
        attrs["user"] = user
        return attrs


# ---------------------------------------------------------- attach / reset

class AddEmailSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, required=False, min_length=8)

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("This email is already in use.")
        return value

    def validate(self, attrs):
        user = self.context["request"].user
        if user.email:
            raise serializers.ValidationError(
                "This account already has an email address."
            )
        # A phone-only account has no usable password, so attaching an email
        # without one would create a login route it can never use.
        if not user.has_usable_password() and not attrs.get("password"):
            raise serializers.ValidationError(
                {"password": "Set a password so you can sign in with this email."}
            )
        if attrs.get("password"):
            try:
                validate_password(attrs["password"], user)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs


class AddPhoneSerializer(serializers.Serializer):
    phone_number = PhoneField()

    def validate_phone_number(self, value):
        if User.objects.filter(phone_number=value).exists():
            raise serializers.ValidationError("This number is already in use.")
        return value

    def validate(self, attrs):
        if self.context["request"].user.phone_number:
            raise serializers.ValidationError(
                "This account already has a phone number."
            )
        return attrs


class ResetPasswordStartSerializer(serializers.Serializer):
    """Accepts either an email or a phone number - the code goes wherever
    the account can actually receive it."""
    identifier = serializers.CharField()


class ResetPasswordConfirmSerializer(serializers.Serializer):
    identifier = serializers.CharField()
    code = serializers.CharField(min_length=4, max_length=8)
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate_new_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value


class SetUsernameSerializer(serializers.Serializer):
    username = serializers.RegexField(
        r"^[a-zA-Z0-9_]{3,15}$",
        error_messages={
            "invalid": "3-15 characters: letters, numbers and underscores only."
        },
    )

    def validate_username(self, value):
        qs = User.objects.filter(username__iexact=value)
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            qs = qs.exclude(pk=request.user.pk)
        if qs.exists():
            raise serializers.ValidationError("This username is already taken.")
        return value


class GoogleSignInSerializer(serializers.Serializer):
    # Named access_token for backwards compatibility with the existing
    # Flutter client, though what Google hands back and what we verify is
    # an ID token.
    access_token = serializers.CharField()


class AuthSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuthSettings
        fields = ["email_otp_required", "updated_at"]
        read_only_fields = ["updated_at"]
