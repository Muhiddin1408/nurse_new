#!/usr/bin/env bash
# Serverda ishga tushiriladi (CI scp qiladi): migrate → up → health; yiqilsa loglar va exit 1.
#   deploy-service.sh <servis> <image_repo> <commit_sha> <migrate|none>
# BLUEPRINT 11: docker compose pull && up -d --no-deps $APP_SERVICES → health tekshiruvi.
# .env va secrets/ serverda turadi — bu skript ularni o'zgartirmaydi.
set -euo pipefail
SERVICE="$1"; IMAGE_REPO="$2"; TAG="$3"; MIGRATE="${4:-migrate}"
DIR="${DEPLOY_ROOT:-/opt/medbron}/$SERVICE"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"

cd "$DIR"
export IMAGE_REPO IMAGE_TAG="$TAG"
# Servis image'idan ishlaydigan barcha konteynerlar (API, relay, consumer, scheduler...) — compose'dan;
# Postgres/Redis va migrate profili tegilmaydi.
mapfile -t APP_SERVICES < <(docker compose config --services | grep -E "^${SERVICE}(-|$)" \
  | grep -vE -- "-(postgres|redis|migrate)$")
if (( ${#APP_SERVICES[@]} == 0 )); then
  echo "XATO: compose'da $SERVICE konteynerlari topilmadi" >&2
  exit 1
fi
echo "==> $SERVICE: $IMAGE_REPO:$TAG"
docker compose pull "${APP_SERVICES[@]}"

if [[ "$MIGRATE" != "none" ]]; then
  echo "==> migrate"
  docker compose --profile migrate run --rm "$SERVICE-migrate"
fi

echo "==> up"
docker compose up -d --no-deps "${APP_SERVICES[@]}"

echo "==> health (${HEALTH_TIMEOUT}s)"
deadline=$((SECONDS + HEALTH_TIMEOUT))
for c in "${APP_SERVICES[@]}"; do
  until [[ "$(docker inspect -f '{{.State.Health.Status}}' "$c" 2>/dev/null)" == "healthy" ]]; do
    if (( SECONDS > deadline )); then
      echo "XATO: $c healthy bo'lmadi" >&2
      docker compose ps
      docker compose logs --tail=200 "${APP_SERVICES[@]}" >&2
      exit 1
    fi
    sleep 3
  done
  echo "    $c: healthy"
done
echo "==> $SERVICE@$TAG deploy qilindi"
