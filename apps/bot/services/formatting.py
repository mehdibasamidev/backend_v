def fa_price(value) -> str:
    formatted = f"{value:,.0f}"
    return formatted.translate(
        str.maketrans(
            "0123456789,",
            "۰۱۲۳۴۵۶۷۸۹٬",
        )
    ) + " تومان"


# Bidi marks. Telegram lays out every line with the Unicode bidi algorithm,
# and these lines mix Persian with Latin (the panel client name):
#   - a Persian digit right after a Latin word is read as part of it, so
#     "A12 — ۱۰ گیگ" came out as "۱۰ — A12 گیگ";
#   - "ℹ️" is itself a strong LEFT-to-right character, so a line starting
#     with it was laid out left to right.
# RLM at the start makes a line right to left; LRM...LRM keeps a Latin name
# whole, and the RLM after it ends its run so what follows isn't pulled in.
RLM = "\u200f"
LRM = "\u200e"

_FA_DIGITS = str.maketrans("0123456789.", "۰۱۲۳۴۵۶۷۸۹٫")


def rtl_line(text):
    """Force a right-to-left line, whatever character it starts with."""
    return f"{RLM}{text}"


def fa_number(value):
    """20.0 -> "۲۰", 1.25 -> "۱٫۲۵": Persian digits, no pointless ".0"."""
    text = f"{value:.2f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)
    return text.translate(_FA_DIGITS)


def client_name(email):
    """The panel client name ("user-6vfa"), safe inside Persian text."""
    return f"{LRM}{email}{LRM}{RLM}" if email else ""


def service_name(subscription):
    return subscription.label or (
        subscription.plan.name if subscription.plan else "پلن سفارشی"
    )


def service_label(subscription):
    """
    "user-6vfa | ۲۰ گیگ ۳۰ روزه" - how one service is named in every bot
    message and button. The panel client name tells two services of the same
    plan apart, and it is what the customer's VPN app shows too.
    """
    if not subscription.xui_client_email:
        return service_name(subscription)
    return f"{client_name(subscription.xui_client_email)} | {service_name(subscription)}"
