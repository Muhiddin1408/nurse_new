# syntax=docker/dockerfile:1.7
# Build konteksti — REPO ILDIZI (libs/py-common kerak):
#   docker build -f backend/search-service/Dockerfile -t medbron/search-service:$(git rev-parse HEAD) .
# Tag = commit SHA, `latest` ishlatilmaydi (BLUEPRINT 1, 11-tamoyil).

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never
WORKDIR /src/backend/search-service
COPY libs/py-common /src/libs/py-common
COPY backend/search-service/pyproject.toml backend/search-service/uv.lock ./
# --no-editable: libs/py-common venv ichiga NUSXA bo'lib o'rnatiladi (final image'da manba yo'q).
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY --chown=app:app backend/search-service/app ./app
COPY --chown=app:app backend/search-service/migrations ./migrations
COPY --chown=app:app backend/search-service/alembic.ini ./alembic.ini
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER app
EXPOSE 8080
# Worker konteynerlari (relay/consumer) compose'da o'z healthcheck'ini (9100 metrics) oladi.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=2)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips=*", "--no-access-log"]
