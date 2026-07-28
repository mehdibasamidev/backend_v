from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.vpn.models import SubscriptionStatusChoices
from apps.vpn.services.xui_client import ThreeXUiClient


def _default_inbound_ids():
    raw = getattr(settings, "XUI_DEFAULT_INBOUND_IDS", "")
    return [int(x) for x in raw.split(",") if x.strip()]


def _build_subscription_link(sub_id):
    base = getattr(settings, "XUI_SUBSCRIPTION_BASE_URL", "").rstrip("/")
    if not base or not sub_id:
        return ""
    return f"{base}/{sub_id}"


def activate_subscription(subscription):
    """
    Called after an admin approves the payment proof for a subscription.
    Creates the client on the 3x-ui panel (attached to every configured
    default inbound at once), then fetches the server-generated uuid/subId
    so we can build the subscription link.
    """
    client = ThreeXUiClient()
    inbound_ids = _default_inbound_ids()
    if not inbound_ids:
        raise ValueError("XUI_DEFAULT_INBOUND_IDS is not configured in settings")

    now = timezone.now()
    expires_at = now + timedelta(days=subscription.duration_days)
    expiry_time_ms = int(expires_at.timestamp() * 1000)
    total_gb_bytes = 0 if subscription.is_unlimited_volume else subscription.volume_gb * (1024 ** 3)
    limit_ip = 0 if subscription.is_unlimited_users else subscription.max_concurrent_users

    client_email = subscription.xui_client_email or (
        f"user{str(subscription.user_id).replace('-', '')[:8]}-{str(subscription.id).replace('-', '')[:8]}"
    )

    client.add_client(
        email=client_email,
        total_gb=total_gb_bytes,
        expiry_time_ms=expiry_time_ms,
        inbound_ids=inbound_ids,
        limit_ip=limit_ip,
    )

    # uuid/subId are generated server-side - fetch them now that the
    # client exists.
    details = client.get_client(client_email)
    xui_client = details.get("client", {})

    subscription.xui_client_email = client_email
    subscription.xui_client_uuid = xui_client.get("uuid", "")
    subscription.xui_client_subid = xui_client.get("subId", "")
    subscription.xui_inbound_ids = inbound_ids
    subscription.subscription_link = _build_subscription_link(subscription.xui_client_subid)
    subscription.started_at = now
    subscription.expires_at = expires_at
    subscription.status = SubscriptionStatusChoices.ACTIVE
    subscription.save()
    return subscription


def reject_subscription(subscription):
    subscription.status = SubscriptionStatusChoices.REJECTED
    subscription.save(update_fields=["status", "updated_at"])
    return subscription


def extend_subscription(subscription, extra_days=0, extra_gb=0):
    """
    For renewals - adds days/GB on top of whatever the client currently
    has on the panel (never resets them). Pass 0 for a dimension that
    isn't changing. Not wired to any endpoint yet - the admin panel will
    call this once we build the renewal action.
    """
    if not subscription.xui_client_email:
        raise ValueError("Subscription has no provisioned client to extend")

    client = ThreeXUiClient()
    extra_bytes = extra_gb * (1024 ** 3) if extra_gb else 0
    client.bulk_adjust(
        emails=[subscription.xui_client_email],
        add_days=extra_days,
        add_bytes=extra_bytes,
    )
    return sync_subscription_usage(subscription)


def get_client_configs(subscription):
    """
    Individual per-location config links (vless://, vmess://, ...) for the
    "view my configs" screen - shown alongside the single subscription link.
    """
    if not subscription.xui_client_email:
        return []
    client = ThreeXUiClient()
    return client.get_links(subscription.xui_client_email)


def sync_subscription_usage(subscription):
    """
    Pulls current traffic usage from the panel and updates local
    bookkeeping. Intended to be called periodically (management command /
    cron / celery beat) for all ACTIVE subscriptions.
    """
    if not subscription.xui_client_email:
        return subscription

    client = ThreeXUiClient()
    traffic = client.get_traffic(subscription.xui_client_email)
    if traffic:
        used = (traffic.get("up", 0) or 0) + (traffic.get("down", 0) or 0)
        subscription.used_traffic_bytes = used
        subscription.last_synced_at = timezone.now()

        if subscription.expires_at and timezone.now() > subscription.expires_at:
            subscription.status = SubscriptionStatusChoices.EXPIRED
        elif not subscription.is_unlimited_volume and subscription.remaining_volume_gb <= 0:
            subscription.status = SubscriptionStatusChoices.EXPIRED

        subscription.save(update_fields=["used_traffic_bytes", "last_synced_at", "status", "updated_at"])
    return subscription
