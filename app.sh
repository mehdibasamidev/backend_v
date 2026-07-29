#!/bin/bash

set -e

echo "🔄 Waiting for PostgreSQL..."

while ! PGPASSWORD=$POSTGRES_PASSWORD psql -h $POSTGRES_HOST -U $POSTGRES_USER -d $POSTGRES_DB -c '\q' 2>/dev/null; do
  echo "⏳ PostgreSQL is unavailable - sleeping"
  sleep 1
done

echo "✅ PostgreSQL is up!"

# Apply migrations
echo "📦 Applying migrations..."
python manage.py migrate --noinput

# Collect static files
echo "📁 Collecting static files..."
python manage.py collectstatic --noinput

# Optional: create superuser. USERNAME_FIELD is email, so --noinput needs
# DJANGO_SUPERUSER_EMAIL + DJANGO_SUPERUSER_PASSWORD (username is in
# REQUIRED_FIELDS, so it's needed too).
if [ -n "$DJANGO_SUPERUSER_EMAIL" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
  echo "👤 Creating superuser (if not exists)..."
  python manage.py createsuperuser --noinput || echo "ℹ️  Superuser already exists or could not be created"
fi

# Register the Telegram webhook. Must re-run whenever the token or the
# public URL changes - otherwise the bot silently receives nothing.
# Never fatal: a webhook problem shouldn't stop the API from serving.
if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_BASE_WEBHOOK_URL" ]; then
  echo "🤖 Registering Telegram webhook..."
  python manage.py telegram_set_webhook || echo "⚠️  Webhook registration failed - check TELEGRAM_* env vars"
fi

# Start server (ASGI via Gunicorn + Uvicorn workers).
# config.asgi (NOT config.wsgi): both the chat websockets and the Telegram
# webhook need ASGI.
echo "🚀 Starting Gunicorn (ASGI mode)..."

exec gunicorn config.asgi:application \
    -k uvicorn_worker.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --workers 3 \
    --timeout 120