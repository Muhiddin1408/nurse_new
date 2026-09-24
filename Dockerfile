# MedBron booking image — API, worker'lar va CronJob'lar BITTA image'dan ishlaydi,
# faqat `command` farq qiladi (k8s/*.yaml).
#
#   docker build -t registry.example.com/medbron/booking:$(git rev-parse --short HEAD) .
#
# Tag = commit. "latest" ishlatilmaydi (k8s/booking-service.yaml izohi).

# ---------------------------------------------------------------------------
# 1-bosqich: wheel'larni yig'ish (kompilyator faqat shu bosqichda)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt

# ---------------------------------------------------------------------------
# 2-bosqich: runtime — kompilyatorsiz, root'siz
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=conf.settings

COPY --from=build /wheels /wheels
RUN pip install --no-index --find-links=/wheels /wheels/* && rm -rf /wheels

RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --chown=app:app . .

# collectstatic PROD rejimida (DJANGO_DEBUG=0) bo'lishi SHART: faqat shunda
# WhiteNoise manifest (staticfiles.json) yaratiladi — usiz prodda admin
# sahifalari "Missing staticfiles manifest entry" bilan 500 beradi.
# `_require_in_prod` talab qiladigan qiymatlar shu buyruq uchun SOXTA —
# settings import vaqtida hech qayerga ulanmaydi, image'da ham qolmaydi.
RUN DJANGO_DEBUG=0 \
    DJANGO_SECRET_KEY=build-only \
    DJANGO_ALLOWED_HOSTS=build \
    POSTGRES_DB=build \
    REDIS_URL=redis://build:6379/0 \
    PAYME_SECRET_KEY=build \
    ESKIZ_EMAIL=build \
    ESKIZ_PASSWORD=build \
    python manage.py collectstatic --noinput

USER app
EXPOSE 8000

# Default — HTTP API. Worker/CronJob'lar k8s'da `command` bilan almashtiradi.
# --graceful-timeout < terminationGracePeriodSeconds (30s): SIGTERM'da joriy
# so'rovlar tugatiladi, keyin pod o'ladi.
CMD ["gunicorn", "conf.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "30", \
     "--graceful-timeout", "25", \
     "--access-logfile", "-"]
