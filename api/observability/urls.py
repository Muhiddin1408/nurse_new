"""Sog'liq tekshiruvi marshrutlari.

⚠️ MANZIL MUHIM: `k8s/booking-service.yaml` dagi probe'lar aynan
`/healthz/live` va `/healthz/ready` ga murojaat qiladi. Shuning uchun bu
URLconf `conf/urls.py` da `healthz/` prefiksi bilan ulanadi — API
versiyalash ostida (`/api/v1/...`) EMAS.

Nega API versiyasi ostida bo'lmasligi kerak:
  1. Kubernetes probe'lari API shartnomasining bir qismi emas — ular
     infratuzilma interfeysi. `/api/v2` chiqqanda probe manzili
     o'zgarmasligi kerak.
  2. Probe'lar autentifikatsiyasiz va throttle'siz ishlashi shart.
     API prefiksi ostida global DRF sozlamalari (`IsAuthenticated`,
     `ScopedRateThrottle`) bexosdan ularga ham tegishi mumkin.
  3. Yuklama ostida throttle probe'ni rad etsa, Kubernetes sog'lom podni
     o'lgan deb hisoblab o'chirib tashlaydi — nosozlik o'z-o'zini
     kuchaytiradi.

Bu view'lar oddiy Django funksiyalari (DRF `APIView` emas) — shu sababli
`REST_FRAMEWORK` sozlamalari ularga umuman ta'sir qilmaydi.
"""

from django.urls import path

from api.observability.health import liveness, readiness, metrics_view

urlpatterns = [
    path("live", liveness, name="healthz-live"),
    path("ready", readiness, name="healthz-ready"),
    # Prometheus scrape manzili. Ingress'da tashqariga OCHILMAYDI (faqat klaster ichida).
    path("metrics", metrics_view, name="metrics"),
]
