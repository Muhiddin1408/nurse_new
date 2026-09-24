"""
URL configuration for conf project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from api.admin_security import TOTPAdminAuthenticationForm

# A12: login formasi TOTP'ni tekshiradi; URL prefiksi muhitdan (ADMIN_URL)
admin.site.login_form = TOTPAdminAuthenticationForm

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path('api/v1/', include('api.v1')),

    # Kubernetes probe'lari. `k8s/booking-service.yaml` aynan shu manzillarga
    # murojaat qiladi, shuning uchun ular API versiyasi ostida EMAS —
    # sababi api/observability/urls.py dagi izohda.
    path('healthz/', include('api.observability.urls')),
]

if settings.SWAGGER_ENABLED:
    urlpatterns += [
        path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
        path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
        path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    ]
