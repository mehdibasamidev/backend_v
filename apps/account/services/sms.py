import logging

import requests
from django.conf import settings

from config.utils.exceptions import AppException

logger = logging.getLogger("apps")

KAVENEGAR_BASE = "https://api.kavenegar.com/v1"


class SmsSendError(AppException):
    default_message = "Could not send the verification code. Please try again."


def send_otp_sms(phone_number, code):
    """
    Sends a verification code through Kavenegar's verify/lookup endpoint.

    verify/lookup rather than sms/send on purpose: Iranian carriers only
    deliver verification messages from a pre-approved template, and lookup
    also reaches numbers that never opted in - which is every new signup.

    The template must already exist and be approved in the Kavenegar panel;
    an unknown one comes back as status 424.

    In DEBUG with no API key configured the code is logged instead of sent,
    so local work doesn't need a real account or burn credit.
    """
    api_key = getattr(settings, "KAVENEGAR_API_KEY", "")
    template = getattr(settings, "KAVENEGAR_OTP_TEMPLATE", "")

    if not api_key or not template:
        if settings.DEBUG:
            logger.warning("SMS disabled - OTP for %s is %s", phone_number, code)
            return {"simulated": True}
        raise SmsSendError(
            "SMS is not configured (KAVENEGAR_API_KEY / KAVENEGAR_OTP_TEMPLATE)."
        )

    url = f"{KAVENEGAR_BASE}/{api_key}/verify/lookup.json"
    params = {
        # Kavenegar expects the local 09xxxxxxxxx form, not E.164.
        "receptor": to_local_format(phone_number),
        "token": code,
        "template": template,
        "type": "sms",
    }

    try:
        response = requests.get(url, params=params, timeout=15)
        payload = response.json()
    except Exception as exc:
        logger.exception("Kavenegar request failed for %s", phone_number)
        raise SmsSendError() from exc

    status = (payload.get("return") or {}).get("status")
    if status != 200:
        message = (payload.get("return") or {}).get("message", "")
        # Logged rather than surfaced: the provider's messages are Persian
        # operational detail ("template not approved", "insufficient
        # credit") that means nothing to the person waiting for a code.
        logger.error(
            "Kavenegar rejected send to %s: status=%s message=%s",
            phone_number,
            status,
            message,
        )
        raise SmsSendError()

    return payload


def to_local_format(phone_number):
    """+989123456789 -> 09123456789"""
    if phone_number.startswith("+98"):
        return "0" + phone_number[3:]
    return phone_number
