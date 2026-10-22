#!/usr/bin/env bash
# Shablondan yangi servis (BLUEPRINT 5: "o'zimcha" tuzilma yo'q).
#
#   scripts/new-service.sh booking-service            # til reestrdan (Py/Go)
#   scripts/new-service.sh booking-service --dest /tmp/x
#
# Servis avval docs/SERVICE_REGISTRY.md ga qo'shilgan bo'lishi SHART (NN, portlar, bog'liqliklar).
# Keyin: lock fayl (uv lock / go mod tidy), testlar, scripts/check-contracts.py.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
OUT="$("$PY" "$ROOT/scripts/_new_service.py" "$@")"
echo "$OUT" | sed '$d'
LANG_KIND="$(echo "$OUT" | tail -1)"
NAME="$1"
DEST="$ROOT/backend/$NAME"
for ((i = 2; i <= $#; i++)); do
  if [[ "${!i}" == "--dest" ]]; then j=$((i + 1)); DEST="${!j}"; fi
done

if [[ "$LANG_KIND" == "py" ]]; then
  if command -v uv >/dev/null 2>&1; then
    (cd "$DEST" && uv lock -q) && echo "  uv.lock yaratildi"
  else
    echo "  DIQQAT: uv topilmadi — '$DEST' ichida 'uv lock' ni ishga tushiring (Dockerfile uv.lock talab qiladi)"
  fi
else
  REL="${DEST#"$ROOT"/}"
  if [[ "$REL" == "$DEST" ]]; then
    echo "  DIQQAT: --dest repo tashqarisida — go.sum uchun '$DEST' ichida 'go mod tidy' ni ishga tushiring"
  elif command -v go >/dev/null 2>&1; then
    (cd "$DEST" && go mod tidy) && echo "  go.sum yaratildi"
  else
    "$ROOT/scripts/go.sh" "$REL" go mod tidy && echo "  go.sum yaratildi (Docker'dagi Go bilan)"
  fi
fi
cat <<MSG

Keyingi qadamlar:
  1. contracts/openapi/$NAME.internal.yaml — internal API kontrakti (koddan OLDIN)
  2. Event handlerlar (katalogdagi har consume uchun), domen modellari, migratsiyalar
  3. scripts/check-contracts.py && scripts/check-registry.py
  4. backend/gateway-kong/kong.*.yml — tashqi route'lar (/internal tashqari)
MSG
