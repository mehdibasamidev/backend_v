import re

from config.utils.exceptions import BadRequestException

# Iranian mobile numbers: 9 followed by nine digits, reachable as
# 09xxxxxxxxx locally or +989xxxxxxxxx internationally.
_IR_MOBILE = re.compile(r"^9\d{9}$")


def normalize_phone(raw):
    """
    Collapses every way a person might type their number into one canonical
    E.164 string.

    Without this, 09123456789 and +989123456789 and ۰۹۱۲۳۴۵۶۷۸۹ would each
    create a separate account for the same phone, and the unique constraint
    would not stop it.
    """
    if not raw:
        raise BadRequestException("Phone number is required.")

    value = str(raw).strip()

    # Persian and Arabic-Indic digits, which Iranian keyboards produce.
    translation = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    value = value.translate(translation)

    value = re.sub(r"[\s\-()]", "", value)

    if value.startswith("+98"):
        core = value[3:]
    elif value.startswith("0098"):
        core = value[4:]
    elif value.startswith("98") and len(value) == 12:
        core = value[2:]
    elif value.startswith("0"):
        core = value[1:]
    else:
        core = value

    if not _IR_MOBILE.match(core):
        raise BadRequestException("Enter a valid Iranian mobile number.")

    return f"+98{core}"
