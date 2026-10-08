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

    `apply_free_days=False` prices without the free-day allowance. Renewals
    no longer use it: a renewal buys the same configuration again at the
    purchase price (renewal_quote), and custom plans always carry volume,
    so the allowance can't make one free.
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


class RenewalUnavailable(BadRequestException):
    """`reason` is a stable code the bot and the app translate."""

    def __init__(self, reason, message):
        self.reason = reason
        super().__init__(message)


def renewal_quote(subscription):
    """
    What renewing this service buys, and what it costs: (days, gb, price).

    One rule for the bot and the app: a renewal buys the service's own plan
    again at today's price, and resets the service to exactly that (see
    provisioning.reset_subscription_period). No periods, no sliders.

      * Fixed plan - the plan's CURRENT days, GB and price, so an admin's
        edit to the plan applies to renewals too. A deactivated or deleted
        plan can't be renewed: it is no longer for sale, so the customer
        buys a current plan instead.
      * Custom - the days/GB/users that were bought, priced exactly as
        buying that configuration today would be (same formula, same
        free-day allowance - a renewal IS buying it again).
    """
    from apps.vpn.models import PlanSourceChoices

    if subscription.source == PlanSourceChoices.FIXED:
        plan = subscription.plan
        if plan is None or not plan.is_active:
            raise RenewalUnavailable(
                "plan_retired",
                "This plan is no longer sold, so it can't be renewed. Please buy one of the current plans.",
            )
        return (
            plan.duration_days,
            0 if plan.is_unlimited_volume else plan.volume_gb,
            plan.price.quantize(Decimal("0.01")),
        )

    volume_gb = subscription.purchased_volume_gb
    if volume_gb is None:
        volume_gb = subscription.volume_gb
    try:
        price = calculate_custom_plan_price(
            volume_gb=volume_gb,
            duration_days=subscription.duration_days,
            max_concurrent_users=max(subscription.max_concurrent_users, 1),
        )
    except (BadRequestException, ValueError) as exc:
        # The pricing ranges were changed since this was bought (or there is
        # no active pricing config): the same configuration can't be sold.
        raise RenewalUnavailable(
            "pricing_unavailable",
            f"This custom service can't be renewed with the current pricing: {exc}",
        ) from exc
    return subscription.duration_days, volume_gb, price
