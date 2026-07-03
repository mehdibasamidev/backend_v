from rest_framework import serializers
from django.contrib.auth import authenticate
# from django.core.exceptions import ValidationError
from apps.account.models import User
from apps.account.serializers.profile import UserInfoSerializer


# ---------------- REGISTER ----------------
class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ["email", "password", "full_name"]

    # def validate_password(self, value):
    #     try:
    #         password_validation.validate_password(value)
    #     except ValidationError as e:
    #         raise serializers.ValidationError(e.messages)
    #     return value

    def create(self, validated_data):

        user = User(
            email=validated_data["email"],
            username=None,
            full_name=validated_data.get("full_name", ""),

        )
        user.set_password(validated_data["password"])
        user.save()
        return user


# ---------------- LOGIN ----------------
class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()

    def validate(self, attrs):
        user = authenticate(
            username=attrs.get("email"),
            password=attrs.get("password"),
        )
        if not user:
            raise serializers.ValidationError("Invalid email or password")
        if not user.is_active:
            raise serializers.ValidationError("Account is disabled")
        return user


# ---------------- GOOGLE ----------------
class GoogleSignInSerializer(serializers.Serializer):
    access_token = serializers.CharField()


# --------------------------------
# Registered User Serializer (Response after Register)
# --------------------------------
class RegisteredUserResponseSerializer(serializers.Serializer):
    access_token = serializers.CharField()
    refresh_token = serializers.CharField()
    status = serializers.CharField()
    user = UserInfoSerializer()


class CheckEmailSerializer(serializers.Serializer):
    email = serializers.EmailField()


# -------------------------------- OTP Verification ----------------
class VerifyOtpSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(min_length=6, max_length=6)
