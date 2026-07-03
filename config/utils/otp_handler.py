# utils/otp_handler.py
import random
import logging
import time
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.core.mail import send_mail
from django.conf import settings

logger = logging.getLogger(__name__)


def generate_and_store_otp(email, user_data):
    # 1. Rate Limiting Check (Resend Protection)
    last_sent = cache.get(f"otp_timestamp_{email}")
    if last_sent and (time.time() - last_sent < 60):
        # We raise an error so the user has to wait
        raise ValidationError("Please wait 60 seconds before requesting a new code.")

    otp = str(random.randint(100000, 999999))

    # 2. Store in cache (Valid for 10 minutes)
    cache_data = {
        "email": email,
        "otp": otp,
        "password": user_data['password'],
        "full_name": user_data.get('full_name', ''),
        "is_coach": user_data.get('is_coach', False),
        "is_gym_owner": user_data.get('is_gym_owner', False),
    }
    cache.set(f"pending_reg_{email}", cache_data, timeout=600)

    # 3. Store timestamp for rate limiting
    cache.set(f"otp_timestamp_{email}", time.time(), timeout=60)
    # 4. Send the actual email
    subject = "Verify your Fitness App account"
    message = f"Your verification code is: {otp} It will expire in 10 minutes."
    from_email = settings.DEFAULT_FROM_EMAIL

    try:
        # TODO
        # In production, you'd use a background task (like Celery) here.
        # For now, this sends it synchronously.
        send_mail(
            subject,
            message,
            from_email,
            [email],
            fail_silently=False,
        )
        logger.info(f"OTP sent successfully to {email}")
    except Exception as e:
        logger.error(f"Failed to send email to {email}: {str(e)}")
        # You might want to raise an exception here so the view returns a 500
        raise e

    return otp
