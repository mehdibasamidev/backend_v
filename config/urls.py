import os
from django.contrib import admin
from django.urls import path, include, re_path
from drf_yasg.views import get_schema_view
from drf_yasg import openapi
from rest_framework import permissions

schema_view = get_schema_view(
    openapi.Info(
        title="VPN Backend API",
        default_version='v1',
        description="REST APIs for VPN Backend",
        contact=openapi.Contact(email="mehdibasami.tech@gmail.com"),
        license=openapi.License(name="MIT License"),
    ),
    url=os.environ.get("SWAGGER_API_URL", "http://localhost:8000") + "/api/v1/",
    public=True,
    permission_classes=(permissions.AllowAny,),
)

urlpatterns = [
    path('admin/', admin.site.urls),

    # API endpoints
    path('api/v1/', include('apps.account.urls')),
    path('api/v1/', include('apps.chat.urls')),

    # Swagger JSON and YAML endpoints
    re_path(r'^swagger(?P<format>\.json|\.yaml)$', schema_view.without_ui(cache_timeout=0), name='schema-json'),

    # Swagger UI
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),

    # Optional: ReDoc UI (cleaner for documentation)
    path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
]
