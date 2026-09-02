# راهنمای استقرار بک‌اند VPN روی VPS

این راهنما بک‌اند Django/ASGI این پروژه را روی یک VPS اوبونتو نصب می‌کند.
سرویس‌های PostgreSQL، Redis، MinIO، Django (با WebSocket) و HAProxy با TLS
اجرا می‌شوند. همهٔ نمونه‌های example.com، YOUR_SERVER_IP و مقدارهای داخل
<> را با مقدار واقعی خودتان جایگزین کنید.

نسخهٔ انگلیسی همین راهنما در README.md قرار دارد.

## پیش‌نیازها

1. یک VPS با Ubuntu 22.04 یا 24.04، حداقل 2 گیگابایت RAM، دو CPU، 25
   گیگابایت فضا، IP عمومی و دسترسی SSH.
2. دو رکورد DNS از نوع A که به IP سرور اشاره می‌کنند:

~~~text
api.example.com    -> YOUR_SERVER_IP
minio.example.com  -> YOUR_SERVER_IP
~~~

3. آدرس Git این مخزن و نام شاخهٔ مورد نظر برای production.
4. رمزها و کلیدهای جدید برای Django، PostgreSQL، MinIO و سرویس‌های جانبی
   فعال مانند Telegram، Kavenegar و 3x-ui.

> **هشدار امنیتی:** فایل‌های .env و .env.prod موجود در مخزن دارای مقادیر
> شبیه credential هستند و .env.prod نیز در Git ثبت شده است. تمام رمزها و
> کلیدهای واقعی آن‌ها را افشاشده در نظر بگیرید و فوراً rotate کنید. این
> فایل‌ها را روی VPS کپی نکنید.

## معماری استقرار

- Django شامل API، WebSocket و webhook تلگرام است و HAProxy آن را به
  127.0.0.1:8000 متصل می‌کند.
- PostgreSQL دیتابیس برنامه است و فقط در شبکهٔ Docker و loopback قابل دسترس
  می‌ماند.
- Redis برای Django Channels استفاده می‌شود و پورت عمومی ندارد.
- MinIO فایل‌های عمومی و رسیدهای خصوصی را نگهداری می‌کند و از طریق HAProxy
  به 127.0.0.1:9000 متصل است.
- HAProxy HTTPS را مدیریت کرده و پورت‌های عمومی 80 و 443 را می‌گیرد.

داده‌های دائمی در volumeهای Docker به نام postgres_data و minio_data هستند.
هرگز در production دستور docker compose down -v را اجرا نکنید؛ -v داده‌ها را
پاک می‌کند.

## مرحلهٔ 1: امن‌سازی و آماده‌سازی VPS

ابتدا به‌صورت root وارد سرور شوید و یک کاربر deployment بسازید:

~~~bash
ssh root@YOUR_SERVER_IP
adduser deploy
usermod -aG sudo deploy
~~~

کلید عمومی SSH خود را در فایل /home/deploy/.ssh/authorized_keys قرار دهید.
در یک terminal جداگانه ورود با کاربر deploy را تست کنید. سپس در
/etc/ssh/sshd_config یا یک فایل داخل /etc/ssh/sshd_config.d/ این دو خط را
اضافه کنید:

~~~text
PermitRootLogin no
PasswordAuthentication no
~~~

قبل از بستن اتصال فعلی root، تنظیمات SSH را بررسی و reload کنید:

~~~bash
sshd -t
systemctl reload ssh
~~~

اکنون با deploy وارد شوید و پکیج‌های اولیه را نصب کنید:

~~~bash
ssh deploy@YOUR_SERVER_IP
sudo apt update
sudo apt -y upgrade
sudo apt install -y ca-certificates curl git ufw haproxy certbot
~~~

Docker Engine و Docker Compose را از مخزن رسمی Docker نصب کنید:

~~~bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
~~~

یک‌بار logout و login کنید تا عضویت گروه Docker اعمال شود، سپس بررسی کنید:

~~~bash
docker version
docker compose version
~~~

فایروال را فعال کنید. ابتدا SSH را مجاز کنید تا دسترسی خود را از دست ندهید:

~~~bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
~~~

پورت‌های 5432، 6379، 8000، 9000 و 9090 را باز نکنید. فایل Compose آن‌ها را
فقط به 127.0.0.1 bind می‌کند. اگر provider فایروال جداگانه دارد، SSH و
پورت‌های 80 و 443 را آنجا نیز مجاز کنید.

## مرحلهٔ 2: دریافت کد پروژه

مسیر ثابت برای پروژه بسازید و مخزن را clone کنید:

~~~bash
sudo mkdir -p /srv/vpn-backend
sudo chown deploy:deploy /srv/vpn-backend
git clone --branch main YOUR_GIT_REPOSITORY_URL /srv/vpn-backend
cd /srv/vpn-backend
git status
~~~

برای مخزن private از deploy key یا GitHub App token استفاده کنید. رمز شخصی
یا token بلندمدت را در آدرس Git و history shell قرار ندهید.

## مرحلهٔ 3: ساخت فایل تنظیمات production

Docker Compose فایل .env را از ریشهٔ پروژه می‌خواند. یک فایل جدید و غیرقابل
دسترسی برای کاربران دیگر بسازید:

~~~bash
cd /srv/vpn-backend
umask 077
nano .env
chmod 600 .env
~~~

متن زیر را در آن قرار دهید و تمام placeholderها را جایگزین کنید. برای ساخت
مقادیر تصادفی از openssl rand -base64 48 استفاده کنید.

~~~dotenv
# Django
DEBUG=False
SECRET_KEY=<new-long-random-django-secret>
ALLOWED_HOSTS=api.example.com,YOUR_SERVER_IP,127.0.0.1
SWAGGER_API_URL=https://api.example.com
ACCESS_TOKEN_LIFETIME_MINUTES=30
REFRESH_TOKEN_LIFETIME_DAYS=1

# PostgreSQL: مقدار HOST داخل Docker باید db باشد
POSTGRES_DB=vpn_backend
POSTGRES_USER=vpn_backend
POSTGRES_PASSWORD=<new-long-random-postgres-password>
POSTGRES_HOST=db
POSTGRES_PORT=5432

# MinIO: endpoint داخلی را تغییر ندهید
MINIO_ROOT_USER=<minio-admin-user>
MINIO_ROOT_PASSWORD=<new-long-random-minio-password>
MINIO_ENDPOINT=http://minio:9000
MINIO_BUCKET_NAME=media
MINIO_PRIVATE_BUCKET_NAME=private

# Email (اختیاری)
EMAIL_HOST=
EMAIL_PORT=587
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_USE_TLS=True

# Stripe (در صورت استفاده)
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=

# اطلاعات پرداخت دستی که به کاربر نمایش داده می‌شود
PAYMENT_CARD_NUMBER=<card-number>
PAYMENT_CARD_HOLDER=<account-holder>

# پنل 3x-ui / X-UI
XUI_PANEL_BASE_URL=https://panel.example.com/<panel-path>/
XUI_API_TOKEN=<x-ui-api-token>
XUI_DEFAULT_INBOUND_IDS=<comma-separated-inbound-ids>
XUI_SUBSCRIPTION_BASE_URL=https://subscriptions.example.com/<path>

# Telegram؛ در صورت عدم استفاده، TOKEN را خالی بگذارید
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=<random-32-plus-character-secret>
TELEGRAM_BASE_WEBHOOK_URL=https://api.example.com
TELEGRAM_ADMIN_GROUP_CHAT_ID=

# سرویس‌های اختیاری
ANTHROPIC_API_KEY=
KAVENEGAR_API_KEY=
KAVENEGAR_OTP_TEMPLATE=
GOOGLE_CLIENT_ID=

# برای غیرفعال‌کردن bypass OTP در production هر دو را خالی بگذارید
TEST_OTP_PHONE_NUMBER=
TEST_OTP_CODE=
~~~

نکته‌های مهم:

- ALLOWED_HOSTS با کاما جدا می‌شود و نباید https:// داشته باشد.
- نام متغیر درست KAVENEGAR_OTP_TEMPLATE است، نه KAVENEGAR_TEMPLATE_NAME که
  در نمونهٔ قدیمی دیده می‌شود.
- DEBUG باید در VPS مقدار False داشته باشد.
- در config/settings.py مقادیر CSRF_TRUSTED_ORIGINS و AWS_S3_CUSTOM_DOMAIN
  فعلاً برای دامنه‌های فعلی hard-code شده‌اند. اگر دامنهٔ دیگری دارید، پیش
  از build آن‌ها را تغییر دهید: اولی https://api.example.com و دومی
  minio.example.com/media باشد. پسوند /media ضروری است.

## مرحلهٔ 4: تنظیم HAProxy و گواهی TLS

پیش از نصب، فایل haproxy.conf را ویرایش کنید:

- ACLهای API و MinIO را به api.example.com و minio.example.com تغییر دهید.
- backend به نام web_back فرض می‌کند frontend روی 127.0.0.1:8081 اجراست.
  اگر frontend در همین VPS است پورت واقعی را وارد کنید؛ و اگر جای دیگری است،
  ACL و backend آن را حذف کنید.
- backendهای Django و MinIO به‌ترتیب باید 127.0.0.1:8000 و
  127.0.0.1:9000 باقی بمانند.

برای صدور اولین گواهی، HAProxy باید متوقف باشد تا Certbot پورت 80 را بگیرد:

~~~bash
sudo systemctl stop haproxy
sudo certbot certonly --standalone +  -d api.example.com -d minio.example.com +  --email you@example.com --agree-tos --no-eff-email
sudo install -d -m 700 /etc/haproxy/certs
sudo sh -c 'cat /etc/letsencrypt/live/api.example.com/fullchain.pem /etc/letsencrypt/live/api.example.com/privkey.pem > /etc/haproxy/certs/bodyremix.pem'
sudo chmod 600 /etc/haproxy/certs/bodyremix.pem
~~~

فایل پیکربندی را نصب، اعتبارسنجی و سرویس را فعال کنید:

~~~bash
sudo cp /srv/vpn-backend/haproxy.conf /etc/haproxy/haproxy.cfg
sudo haproxy -c -f /etc/haproxy/haproxy.cfg
sudo systemctl enable --now haproxy
~~~

برای renew خودکار گواهی، hook زیر را ایجاد کنید. این hook پس از تمدید، فایل
PEM را بازسازی و HAProxy را reload می‌کند:

~~~bash
sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-haproxy.sh > /dev/null <<'EOF'
#!/bin/sh
cat /etc/letsencrypt/live/api.example.com/fullchain.pem /etc/letsencrypt/live/api.example.com/privkey.pem > /etc/haproxy/certs/bodyremix.pem
chmod 600 /etc/haproxy/certs/bodyremix.pem
systemctl reload haproxy
EOF
sudo chmod 700 /etc/letsencrypt/renewal-hooks/deploy/reload-haproxy.sh
sudo certbot renew --dry-run
~~~

بهتر است کنسول MinIO را public نکنید. در زمان نیاز با SSH tunnel به آن وصل
شوید:

~~~bash
ssh -L 9090:127.0.0.1:9090 deploy@YOUR_SERVER_IP
~~~

سپس روی کامپیوتر خود http://localhost:9090 را باز کنید.

## مرحلهٔ 5: build و اجرای برنامه

~~~bash
cd /srv/vpn-backend
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 django
~~~

کانتینر Django ابتدا منتظر PostgreSQL می‌ماند، migration و collectstatic را
اجرا می‌کند، در صورت تنظیم Telegram webhook را ثبت می‌کند، و سپس Gunicorn با
workerهای Uvicorn/ASGI را اجرا می‌کند.

اولین کاربر admin را بسازید:

~~~bash
docker compose exec django python manage.py createsuperuser
~~~

## مرحلهٔ 6: ساخت و امن‌سازی bucketهای MinIO

برنامه به دو bucket نیاز دارد: media عمومی و private خصوصی. تمام فرمان‌ها را
در یک کانتینر موقت MinIO Client اجرا کنید تا alias در همهٔ فرمان‌ها موجود باشد:

~~~bash
cd /srv/vpn-backend
set -a
. ./.env
set +a
docker run --rm --network backend_vpn_vpn_net -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD --entrypoint /bin/sh minio/mc -c '
    mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" &&
    mc mb --ignore-existing local/media &&
    mc mb --ignore-existing local/private &&
    mc anonymous set download local/media &&
    mc anonymous set none local/private
  '
~~~

اگر نام شبکهٔ Docker متفاوت بود با docker network ls آن را پیدا کرده و به‌جای
backend_vpn_vpn_net بنویسید. دسترسی anonymous را هرگز برای private فعال
نکنید.

## مرحلهٔ 7: بررسی صحت استقرار

~~~bash
curl -I http://127.0.0.1:8000/admin/
curl -I https://api.example.com/admin/
curl -I https://minio.example.com/
docker compose ps
docker compose logs --tail=100 django
sudo systemctl status haproxy --no-pager
~~~

با کاربر admin به https://api.example.com/admin/ وارد شوید. Swagger در
https://api.example.com/swagger/ است و نیاز به session مدیر دارد. اگر تلگرام
فعال است webhook را دوباره ثبت یا بررسی کنید:

~~~bash
docker compose exec django python manage.py telegram_set_webhook
~~~

WebSocket را از frontend واقعی تست کنید. HAProxy برای Channels timeout یک‌ساعته
دارد.

## مرحلهٔ 8: زمان‌بندی همگام‌سازی مصرف VPN

به‌عنوان deploy، cron را باز کنید:

~~~bash
crontab -e
~~~

این دو خط را اضافه کنید:

~~~cron
0 * * * * cd /srv/vpn-backend && /usr/bin/docker compose exec -T django python manage.py sync_vpn_usage --limit 300 >> /srv/vpn-backend/sync-vpn.log 2>&1
0 4 * * * cd /srv/vpn-backend && /usr/bin/docker compose exec -T django python manage.py sync_vpn_usage --stale-minutes 0 --include-expired >> /srv/vpn-backend/sync-vpn.log 2>&1
~~~

با command -v docker مسیر واقعی Docker را چک کنید و اگر لازم بود
/usr/bin/docker را تغییر دهید.

## مرحلهٔ 9: انتشار نسخه‌های بعدی

قبل از update از دیتابیس backup بگیرید و سپس image را build مجدد کنید:

~~~bash
cd /srv/vpn-backend
docker compose exec -T db pg_dump -U vpn_backend vpn_backend | gzip > "backup-$(date +%F-%H%M%S).sql.gz"
git fetch origin
git switch main
git pull --ff-only origin main
docker compose up -d --build
docker compose logs --tail=100 django
~~~

اگر haproxy.conf تغییر کرد:

~~~bash
sudo cp haproxy.conf /etc/haproxy/haproxy.cfg
sudo haproxy -c -f /etc/haproxy/haproxy.cfg
sudo systemctl reload haproxy
~~~

بکاپ PostgreSQL و داده‌های MinIO را به فضای رمزنگاری‌شده خارج از VPS منتقل
کنید و restore را روی یک سرور جداگانه تست کنید. نمونهٔ restore دیتابیس:

~~~bash
gunzip -c backup-YYYY-MM-DD-HHMMSS.sql.gz | docker compose exec -T db psql -U vpn_backend vpn_backend
~~~

## عیب‌یابی

- **Django مرتب restart می‌شود:** docker compose logs django را ببینید و
  متغیرهای اجباری و POSTGRES_HOST=db را بررسی کنید.
- **HAProxy خطای 502 می‌دهد:** docker compose ps را چک کنید، سپس curl
  http://127.0.0.1:8000/admin/ را اجرا و پیکربندی HAProxy را validate کنید.
- **Certbot خطا دارد:** اطمینان حاصل کنید DNS به همین VPS اشاره می‌کند و
  پورت 80 هم در UFW و هم در firewall provider باز است.
- **لینک MinIO اشتباه است:** public custom domain باید
  minio.example.com/media باشد و bucket media مجوز download داشته باشد.
- **تلگرام update نمی‌گیرد:** public HTTPS URL و secret را چک کنید و
  telegram_set_webhook را دوباره اجرا کنید.

## اطلاعاتی که باید نهایی شوند

برای شخصی‌سازی کامل دستورها، این موارد را مشخص کنید:

1. نسخهٔ Ubuntu، IP VPS، و دامنه‌های نهایی API و MinIO.
2. آیا frontend روی همین VPS است؟ اگر بله، دامنه و پورت محلی آن چیست؟
3. نصب جدید است یا باید داده‌های PostgreSQL/MinIO فعلی منتقل شوند؟
4. کدام سرویس‌ها فعال هستند: Telegram، Kavenegar، Stripe، Google Sign-In،
   Anthropic و 3x-ui؟
