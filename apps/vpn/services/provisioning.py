import logging
import math
import re
import secrets
import string
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone

from apps.vpn.models import SubscriptionStatusChoices, UserVpnSubscription
from apps.vpn.services.inbounds import resolve_inbound_ids
from apps.vpn.services.xui_client import ThreeXUiClient
from config.utils.exceptions import AppException

logger = logging.getLogger("apps")

# Lowercase letters + digits only. Panel client emails end up inside share
# links and QR codes, so anything ambiguous or non-ASCII is avoided.
_SUFFIX_ALPHABET = string.ascii_lowercase + string.digits
_SUFFIX_LENGTH = 4
_MAX_EMAIL_ATTEMPTS = 12
_MAX_BASE_LENGTH = 32


_GB = 1024 ** 3


def _whole_gb(total_bytes):
    """
    The panel quota as whole GB for volume_gb. 0 stays 0 (unlimited), but
    anything above 0 is at least 1: floor division turned a 0.6 GB quota
    into 0, which this project reads as UNLIMITED - exactly when the
    customer is about to run out. The exact figure lives in
    total_traffic_bytes.
    """
    if not total_bytes:
        return 0
    return max(total_bytes // _GB, 1)


def _build_subscription_link(sub_id):
    base = getattr(settings, "XUI_SUBSCRIPTION_BASE_URL", "").rstrip("/")
    if not base or not sub_id:
        return ""
    return f"{base}/{sub_id}"


def _sanitize_name(value):
    # 3x-ui shows this label everywhere and it ends up in share links, so
    # ASCII only. "_" is allowed because Telegram usernames use it and it
    # is URL-safe; it is trimmed from the ends so "-suffix" reads cleanly.
    cleaned = re.sub(r"[^a-z0-9_]", "", (value or "").lower())
    return cleaned.strip("_")[:_MAX_BASE_LENGTH].rstrip("_")


def _english_name(value):
    # A display name only helps if it reads as a name. Persian (or emoji)
    # sanitises to nothing or to bare digits, which says less than the
    # Telegram id fallback does.
    cleaned = _sanitize_name(value)
    return cleaned if re.search(r"[a-z]", cleaned) else ""


def _base_name_for(user):
    """
    The readable half of the panel client name, first usable match wins.

    Telegram users: Telegram username, account username, English display
    name (spaces dropped), email local part, then "tg<telegram id>" - the
    id is always there and lets an admin find the person in Telegram.

    App-only users: account username, email local part, then "user".
    """
    # Reverse one-to-one; its DoesNotExist is also an AttributeError, so
    # getattr covers "no profile". Read through the relation rather than
    # importing apps.bot, which vpn must not depend on.
    profile = getattr(user, "telegram_profile", None)
    email_local_part = (user.email or "").split("@")[0]

    if profile is None:
        candidates = [user.username, email_local_part]
        fallback = "user"
    else:
        candidates = [
            profile.telegram_username,
            user.username,
            _english_name(user.full_name),
            _english_name(profile.telegram_first_name),
            email_local_part,
        ]
        fallback = f"tg{profile.telegram_user_id}"

    for candidate in candidates:
        cleaned = _sanitize_name(candidate)
        if cleaned:
            return cleaned
    return fallback


def generate_xui_client_email(user, panel_client=None):
    """
    Builds a panel client label like "mehdi_bs-x7k2" or "tg5839201-x7k2".

    The random suffix exists because one person can hold several services
    at once, so the username alone is not unique. On a collision a fresh
    suffix is drawn rather than failing - only an exhausted retry budget
    raises, which in practice means the panel is returning something
    unexpected rather than that we genuinely ran out of names.

    Checks our own table first (cheap) and then the panel (authoritative -
    an admin may have created a client by hand). A panel lookup failure is
    not treated as a collision; the unique constraint still protects us.
    """
    base = _base_name_for(user)

    for _ in range(_MAX_EMAIL_ATTEMPTS):
        suffix = "".join(secrets.choice(_SUFFIX_ALPHABET) for _ in range(_SUFFIX_LENGTH))
        candidate = f"{base}-{suffix}"

        if UserVpnSubscription.objects.filter(xui_client_email=candidate).exists():
            continue

        if panel_client is not None:
            try:
                existing = panel_client.get_client(candidate)
                if existing and existing.get("client"):
                    continue
            except Exception as exc:
                # A 404 here is the normal "not found" case for most panel
                # builds; anything else we log and accept, since the DB
                # constraint is the real guard.
                logger.debug("Panel lookup for %s failed: %s", candidate, exc)

        return candidate

    raise AppException(
        f"Could not generate a free client name for '{base}' after "
        f"{_MAX_EMAIL_ATTEMPTS} attempts. Please retry, or set the client "
        f"name manually on the panel."
    )


def activate_subscription(subscription):
    """
    Called after an admin approves the payment proof for a subscription.
    Creates the client on the 3x-ui panel (attached to every inbound of the
    plan's group at once), then fetches the server-generated uuid/subId so
    we can build the subscription link.
    """
    client = ThreeXUiClient()
    # Resolved now, at approval, from the plan's current group and then
    # frozen into xui_inbound_ids. Moving a plan to another group therefore
    # only affects activations from here on; clients already on the panel
    # keep the inbounds they were created with.
    inbound_ids = resolve_inbound_ids(subscription)

    now = timezone.now()
    expires_at = now + timedelta(days=subscription.duration_days)
    expiry_time_ms = int(expires_at.timestamp() * 1000)
    total_gb_bytes = 0 if subscription.is_unlimited_volume else subscription.volume_gb * (1024 ** 3)
    limit_ip = 0 if subscription.is_unlimited_users else subscription.max_concurrent_users

    # Reuse the existing label on re-activation; otherwise mint one.
    client_email = subscription.xui_client_email or generate_xui_client_email(
        subscription.user, panel_client=client
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
    subscription.total_traffic_bytes = total_gb_bytes
    subscription.started_at = now
    subscription.expires_at = expires_at
    subscription.status = SubscriptionStatusChoices.ACTIVE
    # Only what this function set. `subscription` was loaded before the
    # panel round trips above, and a "Connect Telegram" merge can move it to
    # another account meanwhile; a full save would write the stale user
    # back, leaving a paid service on a deactivated account.
    subscription.save(update_fields=[
        "xui_client_email", "xui_client_uuid", "xui_client_subid",
        "xui_inbound_ids", "subscription_link", "total_traffic_bytes",
        "started_at", "expires_at", "status", "updated_at",
    ])
    return subscription


def reject_subscription(subscription):
    subscription.status = SubscriptionStatusChoices.REJECTED
    subscription.save(update_fields=["status", "updated_at"])
    return subscription


def _read_panel_client(client, email):
    details = client.get_client(email)
    panel_client = details.get("client") or {}
    if not panel_client:
        raise AppException(
            f"Client '{email}' was not found on the panel, so it cannot be renewed."
        )
    return details, panel_client


def panel_period_used_up(details, now=None):
    """
    True when the panel says the current period is over: the quota is
    spent or the expiry has passed. This, not our synced copy, decides
    whether an approved renewal starts now or waits in the queue.
    """
    now = now or timezone.now()
    panel_client = details.get("client") or {}
    total = panel_client.get("totalGB") or 0
    used = details.get("usedTraffic") or 0
    expiry_ms = panel_client.get("expiryTime") or 0
    if total > 0 and used >= total:
        return True
    return 0 < expiry_ms <= int(now.timestamp() * 1000)


def subscription_period_used_up(subscription):
    """Live panel check for one provisioned subscription (network only)."""
    details = ThreeXUiClient().get_client(subscription.xui_client_email)
    return panel_period_used_up(details)


def reset_subscription_period(subscription, days, gb):
    """
    Applies a renewal: the client gets EXACTLY `gb` and `days` from now -
    nothing left over from the previous period is carried across, in either
    direction. That is the business rule: whatever was left belonged to the
    period that was paid for before.

    Expressed as deltas for bulkAdjust (current -> target) plus a traffic
    reset, rather than an absolute updateClient: updateClient replaces the
    whole row and its write schema differs from what getClient returns, so
    echoing a fetched client back fails field by field. Deltas send no
    client payload at all.

    Re-enables the client afterwards: the panel switches a client off when
    its quota or time runs out, and a renewal that left it off would take
    the money and still not connect.
    """
    if not subscription.xui_client_email:
        raise ValueError("Subscription has no provisioned client to renew")

    client = ThreeXUiClient()
    email = subscription.xui_client_email
    details, panel_client = _read_panel_client(client, email)

    current_total = panel_client.get("totalGB") or 0
    current_expiry_ms = panel_client.get("expiryTime") or 0
    now = timezone.now()

    # --- expiry: target is now + days ---
    # bulkAdjust ignores expiryTime == 0 (never expires); a client set up
    # that way by hand keeps it.
    if current_expiry_ms == 0 or days == 0:
        add_days = 0
        new_expires_at = None if current_expiry_ms == 0 else datetime.fromtimestamp(
            current_expiry_ms / 1000, tz=dt_timezone.utc
        )
    else:
        current_expires_at = datetime.fromtimestamp(current_expiry_ms / 1000, tz=dt_timezone.utc)
        delta_seconds = (now + timedelta(days=days) - current_expires_at).total_seconds()
        # Whole days only. Rounded up so the rounding never costs the
        # customer time; at worst they get part of a day extra.
        add_days = math.ceil(delta_seconds / 86400)
        new_expires_at = current_expires_at + timedelta(days=add_days)

    # --- quota: target is exactly gb (0 = unlimited) ---
    target_total = gb * _GB
    if current_total == 0:
        if target_total != 0:
            # bulkAdjust skips unlimited clients, so it can't put a cap on one.
            raise AppException(
                f"Client '{email}' is unlimited on the panel but the plan has a "
                f"{gb} GB quota. Set the quota on the panel by hand, then approve again."
            )
        add_bytes = 0
    else:
        if target_total == 0:
            raise AppException(
                f"The plan is now unlimited but client '{email}' has a quota on the "
                "panel. Remove the quota on the panel by hand, then approve again."
            )
        add_bytes = target_total - current_total

    if add_days or add_bytes:
        client.bulk_adjust(emails=[email], add_days=add_days, add_bytes=add_bytes)
    try:
        client.bulk_reset_traffic([email])
        client.bulk_enable([email])
    except Exception:
        # Not atomic with the adjustment. Leaving the new quota on top of the
        # old meter would shortchange the customer, so undo the adjustment
        # and let the approval be retried.
        logger.exception("Traffic reset/enable failed for %s during renewal; reverting", email)
        if add_days or add_bytes:
            try:
                client.bulk_adjust(emails=[email], add_days=-add_days, add_bytes=-add_bytes)
            except Exception:
                logger.exception("Adjustment revert also failed for %s", email)
        raise

    subscription.volume_gb = _whole_gb(target_total)
    subscription.total_traffic_bytes = target_total
    subscription.used_traffic_bytes = 0
    subscription.expires_at = new_expires_at
    subscription.last_synced_at = now
    subscription.status = SubscriptionStatusChoices.ACTIVE
    subscription.expiry_reminder_sent_at = None
    subscription.low_volume_reminder_sent_at = None
    # Only what this function set - see activate_subscription: a full save
    # after the panel calls could undo a merge that moved the subscription.
    subscription.save(update_fields=[
        "volume_gb", "total_traffic_bytes", "used_traffic_bytes", "expires_at",
        "last_synced_at", "status", "expiry_reminder_sent_at",
        "low_volume_reminder_sent_at", "updated_at",
    ])
    return subscription


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
        if total_bytes != subscription.total_traffic_bytes:
            subscription.total_traffic_bytes = total_bytes
            updated_fields.append("total_traffic_bytes")
        panel_volume_gb = _whole_gb(total_bytes)
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
