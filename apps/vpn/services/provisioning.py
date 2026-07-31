from datetime import datetime, timedelta, timezone as dt_timezone

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


def fetch_client_traffic(client_email, client=None):
    """
    Pure network call - no ORM. Split out from the apply step so several
    clients can be fetched in parallel threads without dragging Django
    database connections into those threads.
    """
    client = client or ThreeXUiClient()
    return client.get_traffic(client_email)


def apply_traffic_to_subscription(subscription, traffic):
    """
    Reconciles a panel payload onto a subscription and saves it.

    The 3x-ui panel is the source of truth: an admin can change a client's
    quota or expiry directly there, and traffic obviously only exists there.
    So this copies usage AND the current limits back, rather than trusting
    whatever was snapshotted at purchase time.

    Must run on the main thread (it writes to the DB).
    """
    if not traffic:
        return subscription

    updated_fields = ["used_traffic_bytes", "last_synced_at", "status", "updated_at"]

    subscription.used_traffic_bytes = (traffic.get("up") or 0) + (traffic.get("down") or 0)
    subscription.last_synced_at = timezone.now()

    # --- quota (bytes; 0 means unlimited, same convention as ours) ---
    total_bytes = traffic.get("total")
    if total_bytes is not None:
        panel_volume_gb = 0 if total_bytes == 0 else total_bytes // (1024 ** 3)
        if panel_volume_gb != subscription.volume_gb:
            subscription.volume_gb = panel_volume_gb
            updated_fields.append("volume_gb")

    # --- expiry (unix ms; 0 means never expires) ---
    expiry_ms = traffic.get("expiryTime")
    if expiry_ms is not None:
        panel_expires_at = (
            None
            if expiry_ms == 0
            else datetime.fromtimestamp(expiry_ms / 1000, tz=dt_timezone.utc)
        )
        if panel_expires_at != subscription.expires_at:
            subscription.expires_at = panel_expires_at
            updated_fields.append("expires_at")

    # --- derive status from the freshly synced numbers ---
    if subscription.expires_at and timezone.now() > subscription.expires_at:
        subscription.status = SubscriptionStatusChoices.EXPIRED
    elif not subscription.is_unlimited_volume and subscription.remaining_volume_gb <= 0:
        subscription.status = SubscriptionStatusChoices.EXPIRED
    elif (
        subscription.status == SubscriptionStatusChoices.EXPIRED
        and traffic.get("enable", True)
    ):
        # An admin topped the client up on the panel - bring it back.
        subscription.status = SubscriptionStatusChoices.ACTIVE

    subscription.save(update_fields=updated_fields)
    return subscription


def sync_subscription_usage(subscription):
    """
    Fetch + apply for a single subscription. Safe to call on any
    subscription - unprovisioned ones are skipped.
    """
    if not subscription.xui_client_email:
        return subscription

    traffic = fetch_client_traffic(subscription.xui_client_email)
    return apply_traffic_to_subscription(subscription, traffic)
