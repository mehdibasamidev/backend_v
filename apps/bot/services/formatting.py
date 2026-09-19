def fa_price(value) -> str:
    formatted = f"{value:,.0f}"
    return formatted.translate(
        str.maketrans(
            "0123456789,",
            "۰۱۲۳۴۵۶۷۸۹٬",
        )
    ) + " تومان"
