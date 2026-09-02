# VPN Backend — VPS deployment guide

This guide deploys this Docker-based Django/ASGI backend on one Ubuntu VPS. It
runs PostgreSQL, Redis, MinIO, Django (with WebSockets), and HAProxy with TLS.
Replace every example.com, YOUR_SERVER_IP, and placeholder value.

## What you need before starting

1. An Ubuntu 22.04 or 24.04 VPS (2 GB RAM, 2 CPUs, and 25 GB storage minimum)
   with a public IP and SSH access.
2. DNS A records pointing to that IP:

~~~text
api.example.com    -> YOUR_SERVER_IP
minio.example.com  -> YOUR_SERVER_IP
~~~

3. The Git URL for this repository and its production branch.
4. New production credentials: Django secret, PostgreSQL password, MinIO
   password, plus credentials for any enabled integrations.

> **Rotate secrets first.** This repository currently includes .env and
> .env.prod files containing credential-like values; .env.prod is tracked by
> Git. Treat all real values in them as exposed, rotate them, and never copy
> those files to the VPS.

## Architecture

- Django serves the API, ASGI WebSockets, and Telegram webhook through HAProxy
  on 127.0.0.1:8000.
- PostgreSQL stores application data and is available only on the Docker
  network and loopback.
- Redis provides the Django Channels broker and is not publicly exposed.
- MinIO stores public media and private payment receipts through HAProxy on
  127.0.0.1:9000.
- HAProxy terminates HTTPS and accepts public traffic on ports 80 and 443.

The Docker volumes postgres_data and minio_data hold persistent data. Never run
docker compose down -v on production.

## 1. Secure and prepare the VPS

Log in as root:

~~~bash
ssh root@YOUR_SERVER_IP
adduser deploy
usermod -aG sudo deploy
~~~

Add your public SSH key to /home/deploy/.ssh/authorized_keys. Test a separate
login as deploy, then disable root/password SSH by adding these lines to
/etc/ssh/sshd_config or /etc/ssh/sshd_config.d/hardening.conf:

~~~text
PermitRootLogin no
PasswordAuthentication no
~~~

Validate and reload SSH without closing the current root session:

~~~bash
sshd -t
systemctl reload ssh
~~~

Log in as deploy and install prerequisites:

~~~bash
ssh deploy@YOUR_SERVER_IP
sudo apt update
sudo apt -y upgrade
sudo apt install -y ca-certificates curl git ufw haproxy certbot
~~~

Install Docker Engine and Compose:

~~~bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
~~~

Log out and in again, then verify Docker:

~~~bash
docker version
docker compose version
~~~

Configure the firewall:

~~~bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
~~~

Do not expose 5432, 6379, 8000, 9000, or 9090. The Compose file binds them to
127.0.0.1 only. If your VPS provider has a cloud firewall, allow SSH, 80, and
443 there as well.

## 2. Get the code

~~~bash
sudo mkdir -p /srv/vpn-backend
sudo chown deploy:deploy /srv/vpn-backend
git clone --branch main YOUR_GIT_REPOSITORY_URL /srv/vpn-backend
cd /srv/vpn-backend
git status
~~~

For a private repository, use a deploy key or GitHub App token. Do not put a
password or long-lived token in the Git URL.

## 3. Configure production secrets

The Compose file loads .env in the project root. Create an untracked,
owner-only file:

~~~bash
cd /srv/vpn-backend
umask 077
nano .env
chmod 600 .env
~~~

Paste this template and replace every placeholder. Generate random secrets with
openssl rand -base64 48.

~~~dotenv
# Django
DEBUG=False
SECRET_KEY=<new-long-random-django-secret>
ALLOWED_HOSTS=api.example.com,YOUR_SERVER_IP,127.0.0.1
SWAGGER_API_URL=https://api.example.com
ACCESS_TOKEN_LIFETIME_MINUTES=30
REFRESH_TOKEN_LIFETIME_DAYS=1

# PostgreSQL: keep HOST=db because this runs inside Docker
POSTGRES_DB=vpn_backend
POSTGRES_USER=vpn_backend
POSTGRES_PASSWORD=<new-long-random-postgres-password>
POSTGRES_HOST=db
POSTGRES_PORT=5432

# MinIO: keep the internal endpoint as shown
MINIO_ROOT_USER=<minio-admin-user>
MINIO_ROOT_PASSWORD=<new-long-random-minio-password>
MINIO_ENDPOINT=http://minio:9000
MINIO_BUCKET_NAME=media
MINIO_PRIVATE_BUCKET_NAME=private

# Email (optional)
EMAIL_HOST=
EMAIL_PORT=587
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_USE_TLS=True

# Optional Stripe
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=

# Manual payment information shown to customers
PAYMENT_CARD_NUMBER=<card-number>
PAYMENT_CARD_HOLDER=<account-holder>

# 3x-ui / X-UI
XUI_PANEL_BASE_URL=https://panel.example.com/<panel-path>/
XUI_API_TOKEN=<x-ui-api-token>
XUI_DEFAULT_INBOUND_IDS=<comma-separated-inbound-ids>
XUI_SUBSCRIPTION_BASE_URL=https://subscriptions.example.com/<path>

# Telegram (leave token blank when unused)
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=<random-32-plus-character-secret>
TELEGRAM_BASE_WEBHOOK_URL=https://api.example.com
TELEGRAM_ADMIN_GROUP_CHAT_ID=

# Optional integrations
ANTHROPIC_API_KEY=
KAVENEGAR_API_KEY=
KAVENEGAR_OTP_TEMPLATE=
GOOGLE_CLIENT_ID=

# Leave both blank to disable the reviewer OTP bypass in production
TEST_OTP_PHONE_NUMBER=
TEST_OTP_CODE=
~~~

Notes:

- ALLOWED_HOSTS is comma-separated and has no https:// prefix.
- The code reads KAVENEGAR_OTP_TEMPLATE, not KAVENEGAR_TEMPLATE_NAME found in
  the old example file.
- Do not set DEBUG=True on the VPS.
- Current settings hard-code CSRF_TRUSTED_ORIGINS and AWS_S3_CUSTOM_DOMAIN for
  existing domains. Before deploying a different domain, update
  config/settings.py: set the CSRF origin to https://api.example.com and the
  MinIO custom domain to minio.example.com/media. The /media suffix is needed.

## 4. Configure HAProxy and TLS

Edit haproxy.conf before installation:

- Change API and MinIO host ACLs to api.example.com and minio.example.com.
- The web_back backend assumes a frontend at 127.0.0.1:8081. Change it to the
  real local frontend port, or remove its ACL/backend if the frontend lives
  elsewhere.
- Keep Django at 127.0.0.1:8000 and MinIO at 127.0.0.1:9000.

Obtain the initial certificate. HAProxy must be stopped while Certbot
standalone owns port 80:

~~~bash
sudo systemctl stop haproxy
sudo certbot certonly --standalone \
  -d api.example.com -d minio.example.com \
  --email you@example.com --agree-tos --no-eff-email
sudo install -d -m 700 /etc/haproxy/certs
sudo sh -c 'cat /etc/letsencrypt/live/api.example.com/fullchain.pem /etc/letsencrypt/live/api.example.com/privkey.pem > /etc/haproxy/certs/bodyremix.pem'
sudo chmod 600 /etc/haproxy/certs/bodyremix.pem
~~~

Install, validate, and start HAProxy:

~~~bash
sudo cp /srv/vpn-backend/haproxy.conf /etc/haproxy/haproxy.cfg
sudo haproxy -c -f /etc/haproxy/haproxy.cfg
sudo systemctl enable --now haproxy
~~~

Make certificate renewals rebuild the PEM file and reload HAProxy:

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

Avoid exposing the MinIO console publicly. Use an SSH tunnel when needed:

~~~bash
ssh -L 9090:127.0.0.1:9090 deploy@YOUR_SERVER_IP
~~~

Then open http://localhost:9090 locally.

## 5. Start the application

~~~bash
cd /srv/vpn-backend
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 django
~~~

At startup, Django waits for PostgreSQL, runs migrations and collectstatic,
optionally registers the Telegram webhook, then starts Gunicorn with Uvicorn
ASGI workers.

Create the first admin:

~~~bash
docker compose exec django python manage.py createsuperuser
~~~

## 6. Create and secure MinIO buckets

This app needs a public media bucket and a private private bucket. Run all
commands in one temporary MinIO Client container so its configured alias is
available for every command:

~~~bash
cd /srv/vpn-backend
set -a
. ./.env
set +a
docker run --rm --network backend_vpn_vpn_net \
  -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD \
  --entrypoint /bin/sh minio/mc -c '
    mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" &&
    mc mb --ignore-existing local/media &&
    mc mb --ignore-existing local/private &&
    mc anonymous set download local/media &&
    mc anonymous set none local/private
  '
~~~

If the generated Docker network has another name, find it with docker network
ls and replace backend_vpn_vpn_net. Never grant anonymous access to private.

## 7. Verify

~~~bash
curl -I http://127.0.0.1:8000/admin/
curl -I https://api.example.com/admin/
curl -I https://minio.example.com/
docker compose ps
docker compose logs --tail=100 django
sudo systemctl status haproxy --no-pager
~~~

Log into https://api.example.com/admin/ with the superuser. Swagger is at
https://api.example.com/swagger/ and requires an admin session. If Telegram is
enabled, re-register/check its webhook:

~~~bash
docker compose exec django python manage.py telegram_set_webhook
~~~

Test WebSockets from the real frontend; HAProxy already has a one-hour tunnel
timeout for Channels.

## 8. Schedule VPN usage synchronization

Add these entries as deploy:

~~~bash
crontab -e
~~~

~~~cron
0 * * * * cd /srv/vpn-backend && /usr/bin/docker compose exec -T django python manage.py sync_vpn_usage --limit 300 >> /srv/vpn-backend/sync-vpn.log 2>&1
0 4 * * * cd /srv/vpn-backend && /usr/bin/docker compose exec -T django python manage.py sync_vpn_usage --stale-minutes 0 --include-expired >> /srv/vpn-backend/sync-vpn.log 2>&1
~~~

Verify Docker's path with command -v docker and adjust the cron commands if
needed.

## 9. Deploy later updates

Back up before each update, then rebuild:

~~~bash
cd /srv/vpn-backend
docker compose exec -T db pg_dump -U vpn_backend vpn_backend | gzip > "backup-$(date +%F-%H%M%S).sql.gz"
git fetch origin
git switch main
git pull --ff-only origin main
docker compose up -d --build
docker compose logs --tail=100 django
~~~

If HAProxy changed:

~~~bash
sudo cp haproxy.conf /etc/haproxy/haproxy.cfg
sudo haproxy -c -f /etc/haproxy/haproxy.cfg
sudo systemctl reload haproxy
~~~

Copy PostgreSQL dumps and MinIO data to off-VPS encrypted storage and test a
restore on another machine. A database restore command is:

~~~bash
gunzip -c backup-YYYY-MM-DD-HHMMSS.sql.gz | docker compose exec -T db psql -U vpn_backend vpn_backend
~~~

## Troubleshooting

- **Django restarts:** run docker compose logs django; check required variables
  and that POSTGRES_HOST=db.
- **HAProxy returns 502:** check docker compose ps, then curl
  http://127.0.0.1:8000/admin/ and validate HAProxy.
- **Certbot fails:** verify DNS points at this VPS and port 80 is allowed in
  both UFW and the provider firewall.
- **MinIO URL is wrong:** ensure the public custom domain is
  minio.example.com/media and the media bucket is downloadable.
- **Telegram gets no updates:** confirm its public HTTPS base URL and secret,
  then rerun telegram_set_webhook.

## Information to confirm

To tailor the commands exactly, please provide:

1. VPS OS/version, public IP, and final API/MinIO domains.
2. Whether a frontend shares this VPS; if yes, its domain and local port.
3. Whether existing PostgreSQL/MinIO data must be migrated or this is a fresh
   install.
4. Which integrations are enabled: Telegram, Kavenegar, Stripe, Google
   Sign-In, Anthropic, and 3x-ui.
