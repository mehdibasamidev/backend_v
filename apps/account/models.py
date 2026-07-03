from uuid import uuid4
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.db.models import Avg


def custom_user_img_upload_to(instance, filename):
    return f"users/{instance.id}/profile_images/{filename}"


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    email = models.EmailField(unique=True, max_length=255)
    username = models.CharField(max_length=150, unique=True, null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    full_name = models.CharField(max_length=255, blank=True)
    profile_picture = models.FileField(upload_to=custom_user_img_upload_to, blank=True)
    biography = models.TextField(blank=True)
    google_id = models.CharField(max_length=255, null=True, blank=True, unique=True)

    USERNAME_FIELD = 'email'  # Email is for LOGIN
    REQUIRED_FIELDS = ['username']  # Username is for the PROFILE

    def __str__(self):
        return self.email

    @property
    def rate(self):
        data = getattr(self, 'received_feedback', None)
        if data:
            avg = data.aggregate(rate=Avg('rating'))['rate']
            return round(avg, 2) if avg else 0.0
        return 0.0
