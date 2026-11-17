import re

from asgiref.sync import sync_to_async
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from apps.bot.services.formatting import fa_decimal_amount, fa_price, rtl_line, service_label
from apps.referral.models import ReferralSettings, RewardKind
from apps.vpn.models import VpnPlan
# Step sizes for the custom-plan +/- steppers. GB uses the step configured in
# VpnPricingConfig (gb_step); days/users don't have a configurable step yet,
# so these are simple constants - bump them here (or add day_step/user_step
# fields to VpnPricingConfig later) if you want finer/coarser control.
DAY_STEP = 5
USER_STEP = 1


REFERRAL_BUTTON = "👥 معرفی به دوستان"
# Telegram sends back the label shown on an existing keyboard, even after
# the admin changes or disables the offer.
REFERRAL_BUTTON_PATTERN = rf"\A{re.escape(REFERRAL_BUTTON)}(?: \(دریافت [۰-۹٬٫]+(?: تومان|٪ تخفیف)\))?\Z"


MENU_ROWS = (
    (
        ("plans", "🚀 خرید سرویس جدید", "menu:plans", "primary"),
        ("renew", "♻️ تمدید سرویس", "menu:renew", "primary"),
    ),
    (("mycode", REFERRAL_BUTTON, "menu:referral", None),),
    (("mysubs", "📲 سرویس‌های من", "menu:subscriptions", None),),
    (("wallet", "💰 اعتبار من", "menu:wallet", None),),
)


def referral_reward_offer():
    policy = ReferralSettings.get_solo()
    if not policy.personal_codes_enabled or policy.reward_value <= 0:
        return ""
    if policy.reward_kind == RewardKind.WALLET:
        return f"{fa_decimal_amount(policy.reward_value)} تومان"
    if policy.reward_kind == RewardKind.DISCOUNT:
        return f"{fa_decimal_amount(policy.reward_value)}٪ تخفیف"
    return ""


async def current_main_keyboard(*, persistent=False):
    offer = await sync_to_async(referral_reward_offer)()
    make_keyboard = main_reply_keyboard if persistent else main_menu_keyboard
    return make_keyboard(offer)


def _menu_label(label, offer):
    return f"{label} (دریافت {offer})" if label == REFERRAL_BUTTON and offer else label


def main_menu_keyboard(offer=""):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_menu_label(label, offer), callback_data=callback, api_kwargs={"style": style} if style else None)
         for _, label, callback, style in row]
        for row in MENU_ROWS
    ])


def main_reply_keyboard(offer=""):
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(_menu_label(label, offer), api_kwargs={"style": style} if style else None)
             for _, label, _, style in row]
            for row in MENU_ROWS
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def subscriptions_keyboard(subscriptions, *, show_refresh=True):
    """Per-service actions; callback payloads stay below Telegram's 64-byte limit."""
    buttons = []
    for subscription in subscriptions:
        # renewal_info is attached by handlers/subscriptions.list_subscriptions.
        if subscription.renewal["available"]:
            buttons.append([
                InlineKeyboardButton(
                    rtl_line(f"تمدید {service_label(subscription)}"),
                    callback_data=f"sub:renew:{subscription.id}",
                )
            ])
    if show_refresh:
        buttons.append([InlineKeyboardButton("🔄 بروزرسانی مصرف", callback_data="sub:refresh")])
    buttons.append([InlineKeyboardButton("⬅️ برگشت", callback_data="menu:main")])
    return InlineKeyboardMarkup(buttons)


def plans_list_keyboard():
    buttons = [
        [InlineKeyboardButton(f"{plan.name} - {fa_price(plan.price)}", callback_data=f"plan:view:{plan.id}")]
        for plan in VpnPlan.objects.filter(is_active=True)
    ]
    buttons.append([InlineKeyboardButton("🧩 پلن سفارشی", callback_data="custom:start")])
    buttons.append([InlineKeyboardButton("⬅️ برگشت", callback_data="menu:main")])
    return InlineKeyboardMarkup(buttons)


def plan_detail_keyboard(plan_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ خرید این پلن", callback_data=f"plan:buy:{plan_id}")],
        [InlineKeyboardButton("⬅️ برگشت", callback_data="menu:plans")],
    ])


def _encode(gb, days, users):
    return f"{gb}:{days}:{users}"


def custom_plan_text(gb, days, users, price):
    try:
        price_line = f"قیمت: {fa_price(price)}"
    except Exception as e:
        price_line = f"⚠️ {e}"
    users_text = "بدون محدودیت" if users == 0 else str(users)
    return (
        "🧩 پلن سفارشی خودت رو بساز:\n\n"
        f"حجم: {gb} گیگ\n"
        f"مدت: {days} روز\n"
        f"کاربر همزمان: {users_text}\n\n"
        f"{price_line}"
    )


def custom_plan_keyboard(gb, days, users, config):
    def clamp(value, lo, hi):
        return max(lo, min(hi, value))

    gb_dec = clamp(gb - config.gb_step, config.min_gb, config.max_gb)
    gb_inc = clamp(gb + config.gb_step, config.min_gb, config.max_gb)
    days_dec = clamp(days - DAY_STEP, config.min_days, config.max_days)
    days_inc = clamp(days + DAY_STEP, config.min_days, config.max_days)
    users_dec = clamp(users - USER_STEP, config.min_users, config.max_users)
    users_inc = clamp(users + USER_STEP, config.min_users, config.max_users)

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➖", callback_data=f"cst:{_encode(gb_dec, days, users)}"),
            InlineKeyboardButton(f"حجم: {gb} گیگ", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"cst:{_encode(gb_inc, days, users)}"),
        ],
        [
            InlineKeyboardButton("➖", callback_data=f"cst:{_encode(gb, days_dec, users)}"),
            InlineKeyboardButton(f"مدت: {days} روز", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"cst:{_encode(gb, days_inc, users)}"),
        ],
        [
            InlineKeyboardButton("➖", callback_data=f"cst:{_encode(gb, days, users_dec)}"),
            InlineKeyboardButton(f"کاربر: {users}", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"cst:{_encode(gb, days, users_inc)}"),
        ],
        [InlineKeyboardButton("✅ ثبت و ادامه", callback_data=f"cstok:{_encode(gb, days, users)}")],
        [InlineKeyboardButton("⬅️ برگشت", callback_data="menu:main")],
    ])
