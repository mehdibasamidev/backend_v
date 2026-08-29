from decouple import config
from django.contrib import admin
from django.urls import path, include, re_path
from drf_yasg.views import get_schema_view
from drf_yasg import openapi
from rest_framework import permissions
from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication

schema_view = get_schema_view(
    openapi.Info(
        title="VPN Backend API",
        default_version='v1',
        description="REST APIs for VPN Backend",
        contact=openapi.Contact(email="mehdibasami.tech@gmail.com"),
        license=openapi.License(name="MIT License"),
    ),
    url=config("SWAGGER_API_URL", default="http://localhost:8000") + "/api/v1/",
    # public=True keeps the full schema visible (rather than filtering it
    # per-user); access itself is gated by permission_classes below.
    public=True,
    # Browsers don't send the JWT header when you just open /swagger/, so
    # SessionAuthentication is what actually lets you in - log into /admin/
    # first and the session cookie carries over.
    authentication_classes=[SessionAuthentication, JWTAuthentication],
    permission_classes=(permissions.IsAdminUser,),
)

urlpatterns = [
    path('admin/', admin.site.urls),

    # API endpoints
    path('api/v1/', include('apps.account.urls')),
    path('api/v1/', include('apps.chat.urls')),
    path('api/v1/', include('apps.vpn.urls')),
    path('api/v1/', include('apps.referral.urls')),
    path('', include('apps.bot.urls')),

    re_path(r'^swagger(?P<format>\.json|\.yaml)$', schema_view.without_ui(cache_timeout=0), name='schema-json'),
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
]
