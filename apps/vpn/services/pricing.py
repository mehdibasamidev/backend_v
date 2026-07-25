from decimal import Decimal

from apps.vpn.models import VpnPricingConfig
from config.utils.exceptions import BadRequestException


def calculate_custom_plan_price(volume_gb: int, duration_days: int, max_concurrent_users: int) -> Decimal:
    """
    Always the single source of truth for custom plan pricing.
    Never trust a price sent from the client - only what this returns.
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
    billable_days = max(duration_days - config.free_days, 0)

    price = (
        config.base_price
        + (Decimal(volume_gb) * config.price_per_gb)
        + (Decimal(billable_days) * config.price_per_extra_days)
        + (Decimal(extra_users) * config.price_per_extra_user)
    )
    return price.quantize(Decimal("0.01"))
