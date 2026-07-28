from pathlib import Path
from decouple import config, Csv
from datetime import timedelta

# BASE DIR
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY
SECRET_KEY = config('SECRET_KEY')
DEBUG = config('DEBUG', cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', cast=Csv())
CSRF_TRUSTED_ORIGINS = ["https://api.bodyremix.ir"]

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# APPLICATION DEFINITION
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # 3rd-party apps
    'rest_framework',
    'rest_framework_simplejwt',
    'drf_yasg',
    'corsheaders',
    'storages',
    'channels',
    'channels_redis',

    # 'parler',  # Uncomment if using Parler for translations

    # local apps

    'apps.account',
    'apps.payments',
    "apps.chat.apps.ChatConfig",
    "apps.vpn",
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',  # CORS
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    # 'django.middleware.locale.LocaleMiddleware', # Uncomment if using Parler for translations
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

# Authentication backends
AUTHENTICATION_BACKENDS = [
    'apps.account.backends.EmailOrUsernameModelBackend',
    'django.contrib.auth.backends.ModelBackend',  # Keep default as fallback
]


TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],  # Optional
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
# Tell Django where the ASGI file is
ASGI_APPLICATION = 'config.asgi.application'
# DATABASE
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('POSTGRES_DB'),
        'USER': config('POSTGRES_USER'),
        'PASSWORD': config('POSTGRES_PASSWORD'),
        'HOST': config('POSTGRES_HOST', '127.0.0.1'),
        'PORT': config('POSTGRES_PORT', default='5432'),
    }
}

# PASSWORD VALIDATION
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# INTERNATIONALIZATION
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# STATIC / MEDIA
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'static'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# DJANGO REST FRAMEWORK
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
}

# SIMPLE JWT SETTINGS
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=config('ACCESS_TOKEN_LIFETIME_MINUTES', default=90, cast=int)),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=config('REFRESH_TOKEN_LIFETIME_DAYS', default=1, cast=int)),
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# SWAGGER (drf_yasg)
SWAGGER_SETTINGS = {
    'USE_SESSION_AUTH': False,
    'SECURITY_DEFINITIONS': {
        'Bearer': {
            'type': 'apiKey',
            'name': 'Authorization',
            'in': 'header'
        }
    }
}

# CORS
CORS_ALLOW_ALL_ORIGINS = True  # Use CORS_ALLOWED_ORIGINS in production

# EMAIL (optional)
# Use this for local development!
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = 'noreply@fitnessapp.com'
# real world
# EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = config('EMAIL_HOST', default='')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)

# DEFAULT AUTO FIELD
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# default file storage
DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'

# minio settings
AWS_ACCESS_KEY_ID = config("MINIO_ROOT_USER", default="minioadminme")
AWS_SECRET_ACCESS_KEY = config("MINIO_ROOT_PASSWORD", default="minioadminme")
AWS_STORAGE_BUCKET_NAME = config("MINIO_BUCKET_NAME", default="media")
AWS_S3_ENDPOINT_URL = config("MINIO_ENDPOINT", default="http://localhost:9000")
AWS_S3_FILE_OVERWRITE = False
AWS_DEFAULT_ACL = None
AWS_S3_ADDRESSING_STYLE = "path"

# parler settings, uncomment if using Parler for translations
# LANGUAGES = [
#     ('en', 'English'),
#     ('fa', 'Farsi'),
#     ('es', 'Spanish'),
#     ('fr', 'French'),
#     ('de', 'German'),
# ]

# PARLER_LANGUAGES = {
#     None: (
#         {'code': 'en'},
#         {'code': 'fa'},
#         {'code': 'fr'},
#     ),
#     'default': {
#         'fallback': 'en',
#         'hide_untranslated': False,
#     }
# }
# End of parler settings


# CUSTOM USER MODEL
AUTH_USER_MODEL = 'account.User'

# Stripe
STRIPE_SECRET_KEY = config("STRIPE_SECRET_KEY", default="")
STRIPE_WEBHOOK_SECRET = config("STRIPE_WEBHOOK_SECRET", default="")
STRIPE_CURRENCY = "usd"
PLATFORM_FEE_PERCENT = 20  # %

# Configure Redis for the channel layer (broadcast system)
# If you don't have Redis installed yet, use 'channels.layers.InMemoryChannelLayer' for testing only.
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            # "hosts": [('127.0.0.1', 6379)],//TODO  its for dev
            "hosts": [('redis', 6379)],  # Use the service name from docker-compose
        },
    },
}

# Payment Card Information (for testing purposes)
PAYMENT_CARD_NUMBER = config("PAYMENT_CARD_NUMBER", default="4242424242424242")
PAYMENT_CARD_HOLDER = config("PAYMENT_CARD_HOLDER", default="N/V")

# X-UI Panel Information
XUI_PANEL_URL = config("XUI_PANEL_URL", default="http://localhost:8080")
XUI_USERNAME = config("XUI_USERNAME", default="admin")
XUI_PASSWORD = config("XUI_PASSWORD", default="admin")
XUI_DEFAULT_INBOUND_ID = config("XUI_DEFAULT_INBOUND_ID", default="")
XUI_SUBSCRIPTION_BASE_URL = config("XUI_SUBSCRIPTION_BASE_URL", default="")
XUI_API_BASE_PATH = config("XUI_API_BASE_PATH", "/panel/api/inbounds")

ANTHROPIC_API_KEY = config("ANTHROPIC_API_KEY", "")  # اختیاری، فقط برای بررسی AI فیش
