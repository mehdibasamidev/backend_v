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


# Bidi marks. Telegram lays out every line with the Unicode bidi algorithm,
# and these labels mix Persian with Latin ("A12", "ali-x7k2"):
#   - a Persian digit right after "A12" is read as part of that Latin run,
#     so "سرویس A12 — ۱۰ گیگ" came out as "سرویس ۱۰ — A12 گیگ";
#   - "ℹ️" is a strong LEFT-to-right character, so a line starting with it
#     was laid out left to right.
# RLM after a Latin token ends its run; RLM at the start of a line makes the
# line right to left; LRM keeps a Latin name together inside its brackets.
RLM = "\u200f"
LRM = "\u200e"


def rtl_line(text):
    """Force a right-to-left line, whatever character it starts with."""
    return f"{RLM}{text}"


def service_code(number):
    """How a service number is shown everywhere: "A1", "A2", ..."""
    return f"A{number}{RLM}"


def client_tag(email):
    """ "(ali-x7k2)" that stays whole and in place inside Persian text."""
    return f"{RLM}({LRM}{email}{LRM}){RLM}" if email else ""


def service_name(subscription):
    return subscription.label or (
        subscription.plan.name if subscription.plan else "پلن سفارشی"
    )


def service_label(subscription, number=None, *, with_client=True):
    """
    "سرویس A2 — P30 (ali-x7k2)": the code tells two services apart in the
    chat, the panel client name is what the customer's VPN app shows.
    """
    if number is None:
        number = service_numbers(subscription.user_id).get(subscription.id)
    head = f"سرویس {service_code(number)} — " if number else ""
    tail = f" {client_tag(subscription.xui_client_email)}" if with_client else ""
    return f"{head}{service_name(subscription)}{tail}"
