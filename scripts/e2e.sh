#!/usr/bin/env bash
# E2E: infra + ko'chirilgan servislar (2: identity, 3: asosiy domen, 4: pul, 5: real-time va qo'shimchalar) + Kong ni Docker'da ko'taradi va
# tests/e2e ni ishga tushiradi.
#
#   scripts/e2e.sh            # ko'tarish (build), migratsiya, admin bootstrap, testlar
#   scripts/e2e.sh --no-build # image'lar tayyor bo'lsa
#   scripts/e2e.sh --down     # hammasini to'xtatish (volume'lar saqlanadi)
#   E2E_PYTEST_ARGS='-k booking' scripts/e2e.sh --no-build
#
# LOKAL/DEV uchun: .env lar scripts/dev-env.py bilan yaratiladi (mavjudlari tegilmaydi).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Tartib: chaqiriladiganlar oldin (sinxron DAG) — booking, keyin payment/billing
SERVICES=(
  file-service admin-service auth-service user-service notification-service
  catalog-service schedule-service pricing-service booking-service
  payment-service billing-service
  review-service dispute-service search-service analytics-service ws-gateway
)
PY="${PYTHON:-python3}"
BUILD=1

for arg in "$@"; do
  case "$arg" in
    --no-build) BUILD=0 ;;
    --down)
      (cd "$ROOT/backend/gateway-kong" && docker compose down)
      for s in "${SERVICES[@]}"; do (cd "$ROOT/backend/$s" && docker compose down); done
      (cd "$ROOT/backend/infra" && docker compose down)
      exit 0 ;;
    *) echo "noma'lum argument: $arg" >&2; exit 2 ;;
  esac
done

echo "==> infra"
[[ -f "$ROOT/backend/infra/.env" ]] || { echo "XATO: backend/infra/.env yo'q (cp .env.example .env)" >&2; exit 1; }
# dlx-bootstrap — bir martalik (exit 0): `--wait` uni "yiqildi" deb hisoblaydi, shuning uchun alohida kutiladi
(cd "$ROOT/backend/infra" && docker compose up -d --wait $(docker compose config --services | grep -vx dlx-bootstrap) \
  && docker compose up --no-deps --exit-code-from dlx-bootstrap dlx-bootstrap >/dev/null)
"$ROOT/scripts/gen-jwt-keys.sh" >/dev/null
"$PY" "$ROOT/scripts/dev-env.py" "${SERVICES[@]}"

for s in "${SERVICES[@]}"; do
  echo "==> $s"
  cd "$ROOT/backend/$s"
  if (( BUILD )); then docker compose build -q; fi
  # Ma'lumot konteynerlari (Postgres / Redis / Elasticsearch / ClickHouse — bor bo'lganlari) va migratsiya (bo'lsa)
  DATA="$(docker compose config --services | grep -xE "$s-(postgres|redis|elasticsearch|clickhouse)" || true)"
  # shellcheck disable=SC2086
  [[ -n "$DATA" ]] && docker compose up -d --wait $DATA
  if docker compose --profile migrate config --services | grep -qx "$s-migrate"; then
    docker compose --profile migrate run --rm "$s-migrate" >/dev/null
  fi
  docker compose up -d --wait
done

echo "==> admin bootstrap"
cd "$ROOT/backend/admin-service"
E2E_ADMIN_LOGIN="e2e-$(date +%s)@medbron.local"
E2E_ADMIN_PASSWORD="E2e-$(openssl rand -hex 8)-Aa1"
URI="$(echo "$E2E_ADMIN_PASSWORD" | docker compose run --rm -T admin-service \
  python -m app.cli create-admin --login "$E2E_ADMIN_LOGIN" --role superadmin --password-stdin | tail -1)"
E2E_ADMIN_TOTP_SECRET="$(echo "$URI" | sed -n 's/.*secret=\([A-Z2-7]*\).*/\1/p')"
[[ -n "$E2E_ADMIN_TOTP_SECRET" ]] || { echo "XATO: admin bootstrap: $URI" >&2; exit 1; }
export E2E_ADMIN_LOGIN E2E_ADMIN_PASSWORD E2E_ADMIN_TOTP_SECRET

# Lokal Payme/Click kalitlari (dev-env.py tasodifiy yaratgan) — testlar webhook'larni shular bilan imzolaydi
E2E_PAYME_KEY="$(sed -n 's/^PAYME_SECRET_KEY=//p' "$ROOT/backend/payment-service/.env")"
E2E_CLICK_KEY="$(sed -n 's/^CLICK_SECRET_KEY=//p' "$ROOT/backend/payment-service/.env")"
export E2E_PAYME_KEY E2E_CLICK_KEY

echo "==> kong"
(cd "$ROOT/backend/gateway-kong" && docker compose up -d --wait)

echo "==> tests/e2e"
cd "$ROOT/tests/e2e"
# shellcheck disable=SC2086
"$PY" -m pytest -q ${E2E_PYTEST_ARGS:-}
