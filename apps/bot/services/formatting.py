def fa_price(value) -> str:
    formatted = f"{value:,.0f}"
    return formatted.translate(
        str.maketrans(
            "0123456789,",
            "۰۱۲۳۴۵۶۷۸۹٬",
        )
    ) + " تومان"


def service_numbers(user_id):
    """
    {subscription_id: n} for every service the user has, numbered in the
    order they were bought (1 = the first). Stable: a new purchase gets the
    next number and never shifts the old ones, so "service 2" in a reminder
    is the same "service 2" in the list - even with the same plan twice.
    Hidden services keep their number for the same reason. ORM - call from
    sync code.
    """
    from apps.vpn.models import UserVpnSubscription

    ids = (
        UserVpnSubscription.objects.filter(user_id=user_id)
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )
    return {subscription_id: index for index, subscription_id in enumerate(ids, start=1)}


def service_name(subscription):
    return subscription.label or (
        subscription.plan.name if subscription.plan else "پلن سفارشی"
    )


def service_label(subscription, number=None, *, with_client=True):
    """
    "سرویس ۲ — P30 (ali-x7k2)": the number tells two services apart in the
    chat, the panel client name is what the customer's VPN app shows.
    """
    if number is None:
        number = service_numbers(subscription.user_id).get(subscription.id)
    head = f"سرویس {number} — " if number else ""
    tail = (
        f" ({subscription.xui_client_email})"
        if with_client and subscription.xui_client_email else ""
    )
    return f"{head}{service_name(subscription)}{tail}"
