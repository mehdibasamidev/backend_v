#!/bin/bash

set -e

echo "🔄 Waiting for PostgreSQL..."

while ! PGPASSWORD=$POSTGRES_PASSWORD psql -h $POSTGRES_HOST -U $POSTGRES_USER -d $POSTGRES_DB -c '\q'; do
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

# Optional: create superuser (only if env vars exist)
if [ "$DJANGO_SUPERUSER_USERNAME" ]; then
  echo "👤 Creating superuser (if not exists)..."
  python manage.py createsuperuser --noinput || true
fi

# Start server (ASGI via Gunicorn + Uvicorn workers)
echo "🚀 Starting Gunicorn (ASGI mode)..."

exec gunicorn config.asgi:application \
    -k uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --workers 3 \
    --timeout 120