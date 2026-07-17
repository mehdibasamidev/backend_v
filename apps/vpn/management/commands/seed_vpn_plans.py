from django.core.management.base import BaseCommand

from apps.vpn.models import VpnPlan

# Prices are stored EXACTLY as given in the reseller's price list (Toman).
# If your list actually means thousands (e.g. "160" = 160,000 Toman),
# multiply the numbers below before running this command.
#
# volume_gb = 0            -> unlimited traffic
# max_concurrent_users = 0 -> unlimited concurrent connections
# (same convention the 3x-ui panel itself uses for totalGB / limitIp)
PLANS = [
    {"name": "۲۰ گیگ ۳۰ روزه", "volume_gb": 20, "duration_days": 30, "max_concurrent_users": 0, "price": 160, "order": 10},
    {"name": "۳۰ گیگ ۳۰ روزه", "volume_gb": 30, "duration_days": 30, "max_concurrent_users": 0, "price": 240, "order": 20},
    {"name": "۵۰ گیگ ۳۰ روزه", "volume_gb": 50, "duration_days": 30, "max_concurrent_users": 0, "price": 390, "order": 30},
    {"name": "۷۰ گیگ ۳۰ روزه", "volume_gb": 70, "duration_days": 30, "max_concurrent_users": 0, "price": 560, "order": 40},
    {"name": "۱۰۰ گیگ ۳۰ روزه", "volume_gb": 100, "duration_days": 30, "max_concurrent_users": 0, "price": 690, "order": 50},
    {
        "name": "تک کاربر نامحدود یک‌ماهه",
        "volume_gb": 0, "duration_days": 30, "max_concurrent_users": 1,
        "price": 490, "order": 60, "is_featured": True,
    },
    {
        "name": "دو کاربره نامحدود یک‌ماهه",
        "volume_gb": 0, "duration_days": 30, "max_concurrent_users": 2,
        "price": 590, "order": 70,
    },
]


class Command(BaseCommand):
    help = "Seed or update the fixed VPN plans from the reseller's current price list."

    def handle(self, *args, **options):
        for data in PLANS:
            plan, created = VpnPlan.objects.update_or_create(
                name=data["name"],
                defaults={
                    "volume_gb": data["volume_gb"],
                    "duration_days": data["duration_days"],
                    "max_concurrent_users": data["max_concurrent_users"],
                    "price": data["price"],
                    "order": data["order"],
                    "is_featured": data.get("is_featured", False),
                    "is_active": True,
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{action}: {plan.name}"))
