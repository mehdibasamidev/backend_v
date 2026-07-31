

# قبلاً ۱۰ دقیقه‌ای بود؛ حالا ساعتی کافیه چون خوانش‌ها خودشون سینک می‌کنن
0 * * * * cd /root/vpn_backend && docker compose exec -T django python manage.py sync_vpn_usage --limit 300

# روزانه: منقضی‌ها رو هم بررسی کن
0 4 * * * cd /root/vpn_backend && docker compose exec -T django python manage.py sync_vpn_usage --stale-minutes 0 --include-expired