from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from apps.vpn.models import VpnPlan
from apps.vpn.services.pricing import calculate_custom_plan_price

# Step sizes for the custom-plan +/- steppers. GB uses the step configured in
# VpnPricingConfig (gb_step); days/users don't have a configurable step yet,
# so these are simple constants - bump them here (or add day_step/user_step
# fields to VpnPricingConfig later) if you want finer/coarser control.
DAY_STEP = 5
USER_STEP = 1


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 خرید سرویس", callback_data="menu:plans")],
        [InlineKeyboardButton("🧩 پلن سفارشی", callback_data="custom:start")],
        [InlineKeyboardButton("📦 سرویس‌های من", callback_data="menu:subscriptions")],
    ])


def plans_list_keyboard():
    buttons = [
        [InlineKeyboardButton(f"{plan.name} - {plan.price} تومان", callback_data=f"plan:view:{plan.id}")]
        for plan in VpnPlan.objects.filter(is_active=True)
    ]
    buttons.append([InlineKeyboardButton("⬅️ برگشت", callback_data="menu:main")])
    return InlineKeyboardMarkup(buttons)


def plan_detail_keyboard(plan_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ خرید این پلن", callback_data=f"plan:buy:{plan_id}")],
        [InlineKeyboardButton("⬅️ برگشت", callback_data="menu:plans")],
    ])


def _encode(gb, days, users):
    return f"{gb}:{days}:{users}"


def custom_plan_text(gb, days, users):
    try:
        price = calculate_custom_plan_price(gb, days, users)
        price_line = f"قیمت: {price} تومان"
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
