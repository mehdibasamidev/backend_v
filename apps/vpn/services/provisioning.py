from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.vpn.models import SubscriptionStatusChoices
from apps.vpn.services.xui_client import ThreeXUiClient


def _build_subscription_link(client_email):
    sub_domain = getattr(settings, "XUI_SUBSCRIPTION_BASE_URL", "").rstrip("/")
    if not sub_domain:
        return ""
    return f"{sub_domain}/sub/{client_email}"


def activate_subscription(subscription):
    """
    Called after an admin approves the payment proof for a subscription.
    Creates the client on the 3x-ui panel and marks the subscription active.
    """
    client = ThreeXUiClient()
    inbound_id = getattr(settings, "XUI_DEFAULT_INBOUND_ID", None)
    if not inbound_id:
        raise ValueError("XUI_DEFAULT_INBOUND_ID is not configured in settings")

    now = timezone.now()
    expires_at = now + timedelta(days=subscription.duration_days)
    expiry_time_ms = int(expires_at.timestamp() * 1000)
    total_gb_bytes = subscription.volume_gb * (1024 ** 3)

    client_email = (
        subscription.xui_client_email
        or f"user{str(subscription.user_id).replace('-', '')[:8]}-{str(subscription.id).replace('-', '')[:8]}"
    )

    client_uuid = client.add_client(
        inbound_id=inbound_id,
        email=client_email,
        total_gb=total_gb_bytes,
        expiry_time_ms=expiry_time_ms,
        limit_ip=subscription.max_concurrent_users,
    )

    subscription.xui_inbound_id = inbound_id
    subscription.xui_client_uuid = client_uuid
    subscription.xui_client_email = client_email
    subscription.subscription_link = _build_subscription_link(client_email)
    subscription.started_at = now
    subscription.expires_at = expires_at
    subscription.status = SubscriptionStatusChoices.ACTIVE
    subscription.save()
    return subscription


def reject_subscription(subscription):
    subscription.status = SubscriptionStatusChoices.REJECTED
    subscription.save(update_fields=["status", "updated_at"])
    return subscription


def sync_subscription_usage(subscription):
    """
    Pulls current traffic usage from the panel and updates local bookkeeping.
    Intended to be called periodically (management command / cron / celery beat)
    for all ACTIVE subscriptions.
    """
    if not subscription.xui_client_email:
        return subscription

    client = ThreeXUiClient()
    traffic = client.get_client_traffic(subscription.xui_client_email)
    if traffic:
        used = (traffic.get("up", 0) or 0) + (traffic.get("down", 0) or 0)
        subscription.used_traffic_bytes = used
        subscription.last_synced_at = timezone.now()

        if subscription.expires_at and timezone.now() > subscription.expires_at:
            subscription.status = SubscriptionStatusChoices.EXPIRED
        elif subscription.remaining_volume_gb <= 0:
            subscription.status = SubscriptionStatusChoices.EXPIRED

        subscription.save(update_fields=["used_traffic_bytes", "last_synced_at", "status", "updated_at"])
    return subscription
