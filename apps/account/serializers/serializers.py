# from rest_framework import serializers
# from django.contrib.auth import password_validation, authenticate
# from django.core.exceptions import ValidationError
# from apps.account.models import (
#     Address, User, UserRole, Trainer, Client, Gym
# )
# import logging

# logger = logging.getLogger(__name__)


# # --------------------------------
# # Address Serializer
# # --------------------------------
# class AddressSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = Address
#         fields = '__all__'


# # ------------------ PROFILE SERIALIZERS ------------------ #
# class GymSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = Gym
#         fields = '__all__'


# class TrainerSerializer(serializers.ModelSerializer):
#     gyms = GymSerializer(many=True, read_only=True)

#     class Meta:
#         model = Trainer
#         exclude = ["user"]


# class ClientSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = Client
#         exclude = ["user"]


# # class AthleteSerializer(serializers.ModelSerializer):
# #     class Meta:
# #         model = Athlete
# #         exclude = ["user"]


# # --------------------------------
# # Registration Serializer
# # --------------------------------
# class RegisterSerializer(serializers.ModelSerializer):
#     password = serializers.CharField(write_only=True)
#     primary_role = serializers.ChoiceField(choices=UserRole.choices())

#     class Meta:
#         model = User
#         fields = ('email', 'password', 'full_name', 'primary_role')

#     def validate_password(self, value):
#         try:
#             password_validation.validate_password(value)
#         except ValidationError as e:
#             raise serializers.ValidationError(str(e))
#         return value

#     def validate_email(self, value):
#         if User.objects.filter(email=value).exists():
#             raise serializers.ValidationError("Email already exists.")
#         return value

#     def create(self, validated_data):
#         user = User(
#             email=validated_data['email'],
#             username=validated_data['email'],
#             full_name=validated_data.get('full_name', ''),
#             primary_role=validated_data['primary_role']
#         )
#         user.set_password(validated_data['password'])
#         user.save()

#         # Create matching profile based on primary role
#         role = validated_data['primary_role']
#         if role == UserRole.TRAINER.value:
#             Trainer.objects.create(user=user)
#         elif role == UserRole.CLIENT.value:
#             Client.objects.create(user=user)

#         return user


# # --------------------------------
# # Login Serializer
# # --------------------------------
# class LoginSerializer(serializers.Serializer):
#     email = serializers.EmailField()
#     password = serializers.CharField()

#     def validate(self, attrs):
#         email = attrs.get("email")
#         password = attrs.get("password")
#         user = authenticate(username=email, password=password)
#         if not user:
#             raise serializers.ValidationError("Invalid email or password")
#         if not user.is_active:
#             raise serializers.ValidationError("Account is disabled")
#         return user


# # --------------------------------
# # User Info Serializer (GET /me)
# # --------------------------------
# class UserInfoSerializer(serializers.ModelSerializer):
#     address = AddressSerializer(required=False)
#     trainer_profile = TrainerSerializer(read_only=True)
#     client_profile = ClientSerializer(read_only=True)
#     owned_gym = GymSerializer(read_only=True)

#     class Meta:
#         model = User
#         exclude = [
#             'password',
#             'is_superuser',
#             'username',
#             'is_staff',
#             'groups',
#             'user_permissions',
#         ]


# # --------------------------------
# # Registered User Serializer (Response after Register)
# # --------------------------------
# class RegisteredUserSerializer(serializers.Serializer):
#     access_token = serializers.CharField()
#     refresh_token = serializers.CharField()
#     status = serializers.CharField()
#     user = UserInfoSerializer()


# # --------------------------------
# # User Profile Update Serializer
# # --------------------------------
# class UserProfileUpdateSerializer(serializers.ModelSerializer):
#     address = AddressSerializer(required=False)

#     class Meta:
#         model = User
#         exclude = [
#             'id', 'email', 'username', 'password', 'last_login',
#             'is_superuser', 'is_staff', 'is_active', 'groups',
#             'user_permissions', 'date_joined'
#         ]

#     def update(self, instance, validated_data):
#         address_data = validated_data.pop("address", None)
#         if address_data:
#             if instance.address:
#                 for key, value in address_data.items():
#                     setattr(instance.address, key, value)
#                 instance.address.save()
#             else:
#                 address = Address.objects.create(**address_data)
#                 instance.address = address

#         for attr, value in validated_data.items():
#             setattr(instance, attr, value)

#         instance.save()
#         return instance


# # --------------------------------
# # Google Sign In Serializer
# # --------------------------------
# class GoogleSignInSerializer(serializers.Serializer):
#     access_token = serializers.CharField(required=True)
