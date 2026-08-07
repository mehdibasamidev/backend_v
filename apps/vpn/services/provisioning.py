import logging
import math
import re
import secrets
import string
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone

from apps.vpn.models import SubscriptionStatusChoices, UserVpnSubscription
from apps.vpn.services.xui_client import ThreeXUiClient
from config.utils.exceptions import AppException

logger = logging.getLogger("apps")

# Lowercase letters + digits only. Panel client emails end up inside share
# links and QR codes, so anything ambiguous or non-ASCII is avoided.
_SUFFIX_ALPHABET = string.ascii_lowercase + string.digits
_SUFFIX_LENGTH = 4
_MAX_EMAIL_ATTEMPTS = 12


def _default_inbound_ids():
    raw = getattr(settings, "XUI_DEFAULT_INBOUND_IDS", "")
    return [int(x) for x in raw.split(",") if x.strip()]


def _build_subscription_link(sub_id):
    base = getattr(settings, "XUI_SUBSCRIPTION_BASE_URL", "").rstrip("/")
    if not base or not sub_id:
        return ""
    return f"{base}/{sub_id}"


def _base_name_for(user):
    """
    Prefers the username; falls back to the email local part, then to a
    literal so bot-registered users (who have no username) still get
    something readable.
    """
    candidate = (user.username or "").strip()
    if not candidate:
        candidate = (user.email or "").split("@")[0]

    # 3x-ui shows this label everywhere - keep it ASCII and predictable.
    candidate = re.sub(r"[^a-z0-9]", "", candidate.lower())
    return candidate[:20] or "user"


def generate_xui_client_email(user, panel_client=None):
    """
    Builds a panel client label like "mehdi-ud4r".

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
    subscription.started_at = now
    subscription.expires_at = expires_at
    subscription.status = SubscriptionStatusChoices.ACTIVE
    subscription.save()
    return subscription


def reject_subscription(subscription):
    subscription.status = SubscriptionStatusChoices.REJECTED
    subscription.save(update_fields=["status", "updated_at"])
    return subscription


# Fields the panel manages itself. get_client returns them, but the update
# endpoint uses a different struct and rejects them - notably `id`, which is
# the numeric DB row id on read while update expects a string there. Echoing
# it back produced:
#   json: cannot unmarshal number into Go struct field Client.id of type string
#
# Casting it to "56" would silence the error and is exactly the wrong fix:
# in the classic 3x-ui schema `id` IS the protocol UUID, so writing a row id
# into it can rewrite the client's identity and break their existing links.
# Dropping it is safe - the URL already identifies the client by email, and
# `uuid` travels in its own field.
def extend_subscription(subscription, extra_days=0, extra_gb=0):
    """
    Applies an approved renewal on the panel.

    Days are RESET, not added: a renewal always buys exactly the window that
    was paid for, starting now. This cuts both ways - renewing while time is
    still on the clock discards the remainder. Deliberate: the rule stays "a
    renewal is exactly N days from today", and the UI warns before they
    confirm.

    Volume is CARRIED OVER: whatever is left is added to the new allowance
    and the counters start at zero. Nineteen of twenty gigabytes used plus a
    twenty gigabyte renewal becomes twenty-one gigabytes with nothing spent.

    Both are expressed as deltas fed to bulkAdjust rather than absolute
    values written with updateClient. updateClient replaces the whole row,
    which means echoing back the object read from getClient - and the two
    endpoints do not share a schema, so every mismatched field surfaces as
    its own `cannot unmarshal ... into Go struct field Client.X` error.
    Deltas avoid sending a client payload at all.
    """
    if not subscription.xui_client_email:
        raise ValueError("Subscription has no provisioned client to extend")

    client = ThreeXUiClient()
    email = subscription.xui_client_email

    details = client.get_client(email)
    panel_client = details.get("client") or {}
    if not panel_client:
        raise AppException(
            f"Client '{email}' was not found on the panel, so it cannot be renewed."
        )

    used_bytes = details.get("usedTraffic") or 0
    current_total = panel_client.get("totalGB") or 0
    current_expiry_ms = panel_client.get("expiryTime") or 0

    now = timezone.now()

    # --- expiry delta ---
    # bulkAdjust skips clients with expiryTime == 0 (never expires), which is
    # what we want - adding a window there would be a downgrade.
    if current_expiry_ms == 0:
        add_days = 0
        new_expires_at = None
    else:
        target = now + timedelta(days=extra_days)
        current_expires_at = datetime.fromtimestamp(
            current_expiry_ms / 1000, tz=dt_timezone.utc
        )
        delta_seconds = (target - current_expires_at).total_seconds()
        # Round up so the rounding to whole days never costs the customer
        # time - at worst they get a few hours extra.
        add_days = math.ceil(delta_seconds / 86400)
        new_expires_at = current_expires_at + timedelta(days=add_days)

    # --- quota delta ---
    # total becomes (total - used) + extra, i.e. leftover plus the new
    # allowance. bulkAdjust skips totalGB == 0 (unlimited), so an unlimited
    # client is left alone automatically.
    if current_total == 0:
        add_bytes = 0
        new_total = 0
        reset_traffic = False
    else:
        extra_bytes = extra_gb * (1024 ** 3)
        add_bytes = extra_bytes - used_bytes
        new_total = current_total + add_bytes
        reset_traffic = True

    if add_days == 0 and add_bytes == 0 and not reset_traffic:
        # Nothing the panel can act on - an unlimited-everything client.
        subscription.status = SubscriptionStatusChoices.ACTIVE
        subscription.save(update_fields=["status", "updated_at"])
        return subscription

    client.bulk_adjust(emails=[email], add_days=add_days, add_bytes=add_bytes)

    if reset_traffic:
        try:
            client.bulk_reset_traffic([email])
        except Exception:
            # The two calls are not atomic. Leaving the raised quota with the
            # old meter would quietly shortchange the customer, so undo the
            # adjustment and let the renewal be retried.
            logger.exception(
                "Traffic reset failed for %s during renewal; reverting adjustment",
                email,
            )
            try:
                client.bulk_adjust(
                    emails=[email], add_days=-add_days, add_bytes=-add_bytes
                )
            except Exception:
                logger.exception("Adjustment revert also failed for %s", email)
            raise

    subscription.volume_gb = 0 if new_total == 0 else new_total // (1024 ** 3)
    subscription.used_traffic_bytes = 0 if reset_traffic else used_bytes
    subscription.expires_at = new_expires_at
    subscription.last_synced_at = now
    subscription.status = SubscriptionStatusChoices.ACTIVE
    subscription.save()
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
