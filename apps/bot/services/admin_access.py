"""
Who is allowed to review a payment receipt, and where receipts get posted.

Two ways to be a reviewer:

  - TELEGRAM_ADMIN_GROUP_CHAT_ID   a real group; whoever is administrator
                                   or creator there can review.
  - TELEGRAM_ADMIN_USER_IDS        named people; each gets the receipt in a
                                   private chat with the bot.

Both can be set at once. Telegram reports no admins in a private chat -
get_chat_member() there answers "member" - so a private chat id in the
group setting alone can never authorise anyone.
"""

from django.conf import settings


def admin_chat_ids() -> list[str]:
    """
    Every chat a new receipt is posted to, in order, without duplicates.

    Deduped because the same id can legitimately appear twice: someone
    testing with their own private chat as the "group" will also be in
    TELEGRAM_ADMIN_USER_IDS, and two copies of the same receipt means two
    sets of buttons to press.
    """
    chat_ids: list[str] = []

    group_chat_id = str(settings.TELEGRAM_ADMIN_GROUP_CHAT_ID or "").strip()
    if group_chat_id:
        chat_ids.append(group_chat_id)

    for user_id in settings.TELEGRAM_ADMIN_USER_IDS:
        if str(user_id) not in chat_ids:
            chat_ids.append(str(user_id))

    return chat_ids


def is_admin_user(user_id) -> bool:
    return str(user_id) in {str(uid) for uid in settings.TELEGRAM_ADMIN_USER_IDS}


def is_admin_group(chat_id) -> bool:
    group_chat_id = str(settings.TELEGRAM_ADMIN_GROUP_CHAT_ID or "").strip()
    return bool(group_chat_id) and str(chat_id) == group_chat_id


async def check_can_review(bot, *, chat_id, user_id) -> str | None:
    """
    Return None when this person may review from this chat, otherwise the
    message to show them.

    The chat is checked first so a review button forwarded or copied into
    some other chat is inert even when an admin presses it.
    """
    if not is_admin_group(chat_id) and str(chat_id) not in admin_chat_ids():
        return "این دکمه فقط توی چت ادمین‌ها کار می‌کنه."

    if is_admin_user(user_id):
        return None

    if is_admin_group(chat_id):
        member = await bot.get_chat_member(settings.TELEGRAM_ADMIN_GROUP_CHAT_ID, user_id)
        if member.status in ("administrator", "creator"):
            return None
        return "فقط ادمین‌های گروه می‌تونن تایید/رد کنن."

    # A private chat belonging to someone no longer on the admin list.
    return "دسترسی تایید/رد نداری."
