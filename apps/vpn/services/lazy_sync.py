import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.conf import settings
from django.utils import timezone

from apps.vpn.models import SubscriptionStatusChoices
from apps.vpn.services.provisioning import (
    apply_traffic_to_subscription,
    fetch_client_traffic,
)

logger = logging.getLogger("apps")


def _max_age_seconds():
    return getattr(settings, "VPN_LAZY_SYNC_MAX_AGE_SECONDS", 60)


def _is_enabled():
    return getattr(settings, "VPN_LAZY_SYNC_ENABLED", True)


def _needs_sync(subscription, max_age):
    """
    Only ACTIVE, provisioned subscriptions whose local copy has gone stale.

    Expired/rejected ones are skipped on read paths on purpose: they change
    rarely, and reviving one is the periodic command's job - not something
    worth a panel round-trip on every dashboard load.
    """
    if not subscription.xui_client_email:
        return False
    if subscription.status != SubscriptionStatusChoices.ACTIVE:
        return False
    if subscription.last_synced_at is None:
        return True
    return (timezone.now() - subscription.last_synced_at).total_seconds() > max_age


def lazy_sync(subscriptions):
    """
    Refreshes a page of subscriptions straight from the panel before they're
    serialized, so the user sees live numbers instead of whatever the last
    cron run wrote.

    Three things keep this from becoming a liability:

      * Throttled - a subscription synced within VPN_LAZY_SYNC_MAX_AGE_SECONDS
        is left alone, so hammering pull-to-refresh doesn't hammer the panel.
      * Parallel - fetches run in a small thread pool, so a page of 10 costs
        roughly one round-trip instead of ten. Only the HTTP calls are
        threaded; every DB write happens back on the request thread.
      * Non-fatal - if the panel is slow or down, the cached values are
        served as-is. A dashboard showing five-minute-old traffic beats a
        dashboard showing an error.

    Mutates and returns the list that was passed in.
    """
    if not _is_enabled() or not subscriptions:
        return subscriptions

    max_age = _max_age_seconds()
    stale = [s for s in subscriptions if _needs_sync(s, max_age)]
    if not stale:
        return subscriptions

    max_workers = min(len(stale), getattr(settings, "VPN_LAZY_SYNC_WORKERS", 5))
    results = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(fetch_client_traffic, s.xui_client_email): s
            for s in stale
        }
        for future in as_completed(futures):
            subscription = futures[future]
            try:
                results[subscription.id] = future.result()
            except Exception as exc:
                # Never surface panel trouble to the customer - fall back to
                # the values we already have.
                logger.warning(
                    "Lazy sync failed for %s (%s): %s",
                    subscription.id,
                    subscription.xui_client_email,
                    exc,
                )

    for subscription in stale:
        traffic = results.get(subscription.id)
        if not traffic:
            continue
        try:
            apply_traffic_to_subscription(subscription, traffic)
        except Exception as exc:
            logger.warning(
                "Could not apply synced traffic for %s: %s", subscription.id, exc
            )

    return subscriptions
