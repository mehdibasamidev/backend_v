from decimal import Decimal

from apps.vpn.models import VpnPricingConfig
from config.utils.exceptions import BadRequestException


def calculate_custom_plan_price(
    volume_gb: int,
    duration_days: int,
    max_concurrent_users: int,
    apply_free_days: bool = True,
) -> Decimal:
    """
    Always the single source of truth for custom plan pricing.
    Never trust a price sent from the client - only what this returns.

    `apply_free_days` exists because free_days is a FIRST-PURCHASE
    allowance. Charging renewals the same way made a 30-day renewal cost
    nothing (billable_days = 30 - 30 = 0), which is fatal for a plan with
    no volume to charge for either. Renewals pass False.
    """
    config = VpnPricingConfig.get_active()

    if not (config.min_gb <= volume_gb <= config.max_gb):
        raise BadRequestException(f"Volume must be between {config.min_gb} and {config.max_gb} GB")
    if volume_gb % config.gb_step != 0:
        raise BadRequestException(f"Volume must be a multiple of {config.gb_step} GB")

    if not (config.min_days <= duration_days <= config.max_days):
        raise BadRequestException(f"Duration must be between {config.min_days} and {config.max_days} days")

    if not (config.min_users <= max_concurrent_users <= config.max_users):
        raise BadRequestException(f"Concurrent users must be between {config.min_users} and {config.max_users}")

    extra_users = max(max_concurrent_users - 1, 0)
    billable_days = max(duration_days - config.free_days, 0) if apply_free_days else duration_days

    price = (
        config.base_price
        + (Decimal(volume_gb) * config.price_per_gb)
        + (Decimal(billable_days) * config.price_per_extra_days)
        + (Decimal(extra_users) * config.price_per_extra_user)
    )
    return price.quantize(Decimal("0.01"))


def resolve_renewal(subscription, periods=None, extra_days=None, extra_gb=None):
    """
    Works out what a renewal actually adds, and what it costs.
    Returns (extra_days, extra_gb, price).

    A subscription bought from a fixed plan renews in whole plan periods
    priced at the plan's CURRENT price - not via the custom-plan formula.
    That keeps admin price changes authoritative and stops unlimited plans
    (no volume, and free_days swallowing the duration) from renewing for
    almost nothing.

    Custom subscriptions keep their sliders, but are priced with no
    free-day allowance for the same reason.
    """
    plan = subscription.plan

    if plan is not None and plan.is_active:
        count = periods or 1
        return (
            plan.duration_days * count,
            0 if plan.is_unlimited_volume else plan.volume_gb * count,
            (plan.price * count).quantize(Decimal("0.01")),
        )

    # Custom, or the original plan is gone/retired - fall back to the
    # snapshot on the subscription and current unit rates.
    days = extra_days if extra_days is not None else subscription.duration_days
    if subscription.is_unlimited_volume:
        gb = 0
    else:
        gb = extra_gb if extra_gb is not None else subscription.volume_gb

    users = max(subscription.max_concurrent_users, 1)

    if gb == 0:
        # Duration-only top-up: the volume validation in
        # calculate_custom_plan_price would reject 0, so price the pieces
        # that apply directly.
        config = VpnPricingConfig.get_active()
        if days <= 0:
            raise BadRequestException("Add at least some days or volume.")
        price = (
            config.base_price
            + (Decimal(days) * config.price_per_extra_days)
            + (Decimal(max(users - 1, 0)) * config.price_per_extra_user)
        ).quantize(Decimal("0.01"))
        return days, 0, price

    price = calculate_custom_plan_price(
        volume_gb=gb,
        duration_days=days,
        max_concurrent_users=users,
        apply_free_days=False,
    )
    return days, gb, price
