import logging

import requests
from django.db import transaction
from django.db.models import Count, ProtectedError, Q
from django.utils import timezone

from apps.vpn.models import InboundGroup, XuiInbound
from apps.vpn.services.xui_client import ThreeXUiClient, XuiApiException
from config.utils.exceptions import AppException, BadRequestException

logger = logging.getLogger("apps")


class InboundSyncError(BadRequestException):
    default_message = "Could not read inbounds from the 3x-ui panel"


# ---------------------------------------------------------------------------
# Mirror of the panel's inbounds
# ---------------------------------------------------------------------------

def fetch_panel_inbounds(client=None):
    """Pure network call - no ORM, same split as fetch_client_traffic."""
    client = client or ThreeXUiClient()
    return client.list_inbounds()


def _panel_error_reason(exc):
    """
    One short line for the admin. Never str() of a requests exception: it
    carries the full panel URL, including the panel's secret base path.
    """
    if isinstance(exc, XuiApiException):
        return exc.message
    if isinstance(exc, requests.Timeout):
        return "the panel did not answer in time"
    if isinstance(exc, requests.ConnectionError):
        return "could not connect to the panel (check XUI_PANEL_BASE_URL)"
    if isinstance(exc, requests.HTTPError):
        status = getattr(exc.response, "status_code", "?")
        if status in (401, 403):
            return f"the panel refused the API token (HTTP {status})"
        if status == 404:
            # 3x-ui 3.5-3.7 answer a bad token with 404, not 401.
            return "HTTP 404 - wrong XUI_PANEL_BASE_URL path, or the API token was refused"
        return f"the panel answered HTTP {status}"
    if isinstance(exc, ValueError):
        return "the panel's answer was not JSON (check XUI_PANEL_BASE_URL)"
    return exc.__class__.__name__


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sync_inbounds_from_panel(client=None):
    """
    Upserts the local mirror from the panel and returns every row, ordered
    by panel_id.

    Rows the panel no longer returns are flagged exists_on_panel=False,
    never deleted - see XuiInbound. Raises InboundSyncError (a 400 through
    the exception handler) when the panel cannot be read or returns no
    inbounds at all.
    """
    # Network first and outside the transaction: a slow panel must not
    # hold row locks.
    try:
        rows = fetch_panel_inbounds(client)
    except Exception as exc:
        logger.exception("Reading inbounds from the 3x-ui panel failed")
        raise InboundSyncError(
            f"Could not read inbounds from the 3x-ui panel: {_panel_error_reason(exc)}"
        ) from exc

    if not rows:
        # Applying an empty list would flag every mirrored inbound missing,
        # and from then on every approval fails with "group has no
        # inbounds". A panel with no inbounds serves nobody anyway, so an
        # empty answer is far more likely a scoped token or a panel hiccup
        # than the truth - keep the mirror as it is and say so.
        raise InboundSyncError(
            "Could not read inbounds from the 3x-ui panel: it returned an empty "
            "list, so nothing was changed. Check the panel and the API token."
        )

    now = timezone.now()
    seen = set()
    with transaction.atomic():
        existing = {
            inbound.panel_id: inbound
            for inbound in XuiInbound.objects.select_for_update()
        }
        for row in rows:
            panel_id = _as_int(row.get("id"))
            if panel_id is None or panel_id < 0:
                logger.warning("Skipping panel inbound with unusable id %r", row.get("id"))
                continue
            seen.add(panel_id)

            port = _as_int(row.get("port"))
            fields = {
                "remark": str(row.get("remark") or "")[:200],
                "protocol": str(row.get("protocol") or "")[:32],
                "port": port if port and port > 0 else None,
                "is_enabled_on_panel": bool(row.get("enable", True)),
                "exists_on_panel": True,
                "last_synced_at": now,
            }

            inbound = existing.get(panel_id)
            if inbound is None:
                # Not a plain create(): select_for_update() above only locks
                # rows that already existed, so a sync running at the same
                # time (two admins pressing Sync, or the button and
                # `manage.py sync_xui_inbounds`) may have inserted this id
                # since. create() would then hit the unique panel_id and 500.
                XuiInbound.objects.update_or_create(panel_id=panel_id, defaults=fields)
                continue

            # The panel can hand an id out again (deleting the last inbound
            # resets its sequence, and so does restoring a backup). A
            # protocol change on the same id is the visible symptom.
            if inbound.protocol and fields["protocol"] and inbound.protocol != fields["protocol"]:
                logger.warning(
                    "Panel inbound #%s changed protocol %s -> %s; if it is a different "
                    "server now, check the inbound groups that use it",
                    panel_id, inbound.protocol, fields["protocol"],
                )
            for name, value in fields.items():
                setattr(inbound, name, value)
            inbound.save()

        XuiInbound.objects.exclude(panel_id__in=seen).filter(exists_on_panel=True).update(
            exists_on_panel=False, updated_at=now,
        )

    return list(XuiInbound.objects.order_by("panel_id"))


# ---------------------------------------------------------------------------
# Which inbounds a subscription is provisioned on
# ---------------------------------------------------------------------------

def inbound_group_for(subscription):
    plan = subscription.plan
    if plan is not None and plan.inbound_group_id:
        return plan.inbound_group
    return InboundGroup.get_default()


def resolve_inbound_ids(subscription):
    """Sorted panel ids to create this subscription's client on."""
    group = inbound_group_for(subscription)
    panel_ids = sorted(
        group.inbounds.filter(exists_on_panel=True).values_list("panel_id", flat=True)
    )
    if not panel_ids:
        raise AppException(
            f"Inbound group '{group.name}' has no inbounds that exist on the panel. "
            "Add inbounds to it in the admin panel (Inbounds tab), then approve again."
        )
    return panel_ids


# ---------------------------------------------------------------------------
# Group management (admin API and Django admin)
# ---------------------------------------------------------------------------

def inbound_groups_with_details():
    """What the admin API serializes: inbounds prefetched, plans counted."""
    return (
        InboundGroup.objects
        .annotate(plan_count=Count("plans", distinct=True))
        .prefetch_related("inbounds")
        # Explicit: Django drops Meta.ordering from aggregate (GROUP BY)
        # queries, so the default-first order would silently disappear.
        .order_by("-is_default", "name")
    )


@transaction.atomic
def make_default_group(group):
    # Lock the outgoing default and the incoming one together so two
    # concurrent switches queue up instead of racing, and clear the old
    # flag BEFORE setting the new one - the partial unique constraint
    # would reject two defaults even for an instant.
    list(InboundGroup.objects.select_for_update().filter(Q(is_default=True) | Q(pk=group.pk)))
    now = timezone.now()
    InboundGroup.objects.filter(is_default=True).exclude(pk=group.pk).update(
        is_default=False, updated_at=now,
    )
    InboundGroup.objects.filter(pk=group.pk).update(is_default=True, updated_at=now)
    group.is_default = True
    group.updated_at = now
    return group


@transaction.atomic
def create_inbound_group(*, name, inbounds, is_default=False):
    group = InboundGroup.objects.create(name=name)
    group.inbounds.set(inbounds)
    if is_default:
        make_default_group(group)
    return group


@transaction.atomic
def update_inbound_group(group, *, name=None, inbounds=None, is_default=None):
    """PATCH semantics: None leaves that part alone."""
    if is_default is False and group.is_default:
        # Unsetting would leave custom plans with nowhere to provision.
        # The serializer reports this on the field; this guards other callers.
        raise BadRequestException("Make another group the default instead.")

    if name is not None:
        group.name = name
    if inbounds is not None:
        group.inbounds.set(inbounds)
    # Saved even when only the inbounds changed, so updated_at moves.
    group.save(update_fields=["name", "updated_at"])

    if is_default and not group.is_default:
        make_default_group(group)
    return group


def delete_inbound_group(group):
    if group.is_default:
        raise BadRequestException(
            "This is the default group. Make another group the default before deleting it."
        )
    plan_names = list(group.plans.order_by("name").values_list("name", flat=True))
    if plan_names:
        raise _group_in_use(plan_names)
    try:
        group.delete()
    except ProtectedError:
        # A plan was pointed at it after the check above (on_delete=PROTECT).
        raise _group_in_use(list(group.plans.order_by("name").values_list("name", flat=True)))


def _group_in_use(plan_names):
    return BadRequestException(
        f"Plans still use this group: {', '.join(plan_names)}. "
        "Move them to another group first."
    )
