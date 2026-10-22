#!/usr/bin/env bash
# RS256 kalit juftliklarini yaratadi (faqat LOKAL; `secrets/` .gitignore'da).
#
#   scripts/gen-jwt-keys.sh            # secrets/ ga
#   scripts/gen-jwt-keys.sh /tmp/keys  # boshqa papkaga
#
#   public/jwt-public.pem,  private/jwt-private.pem         — auth-service (user JWT)
#   public/admin-jwt-public.pem, private/admin-jwt-private.pem — admin-service (admin JWT)
#   private/auth-service.env  (JWT_PRIVATE_KEY=<base64>)       — auth-service API konteyneri uchun
#   private/admin-service.env (ADMIN_JWT_PRIVATE_KEY=<base64>) — admin-service API konteyneri uchun
#
# Servislar faqat `public/` papkani `/secrets` ga read-only ulaydi. Imzo kaliti imzolovchi servisning
# API konteyneriga compose `env_file` orqali beriladi (fayl bind-mount emas: konteyner uid 10001 host'dagi
# 0600 faylni o'qiy olmaydi, env_file'ni esa compose host foydalanuvchisi huquqi bilan o'qiydi).
# Prod kalitlari serverda / vault'da yaratiladi va HECH QACHON repoga tushmaydi.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/secrets}"
mkdir -p "$OUT/public" "$OUT/private"
chmod 700 "$OUT" "$OUT/private"
chmod 755 "$OUT/public"

gen() {
  local name="$1"
  local private="$OUT/private/$name-private.pem" public="$OUT/public/$name-public.pem"
  if [[ -e "$private" ]]; then
    echo "mavjud, tegilmadi: $private (rotatsiya: yangi kalit + eski public'ni public faylga qo'shib qoldiring)"
    return
  fi
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$private" 2>/dev/null
  openssl pkey -in "$private" -pubout -out "$public"
  chmod 600 "$private"
  chmod 644 "$public"
  echo "yaratildi: $private, $public"
}

# <servis>.env — mavjud private kalitdan (kalit qayta yaratilmaydi; env fayl har safar yangilanadi)
env_file() {
  local name="$1" service="$2" var="$3"
  local target="$OUT/private/$service.env"
  (umask 077; printf '# %s — scripts/gen-jwt-keys.sh yaratgan. Commit QILINMAYDI.\n%s=%s\n' \
    "$service" "$var" "$(base64 -w0 "$OUT/private/$name-private.pem")" > "$target")
  echo "env: $target"
}

gen jwt
gen admin-jwt
env_file jwt auth-service JWT_PRIVATE_KEY
env_file admin-jwt admin-service ADMIN_JWT_PRIVATE_KEY

if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 && [[ "$OUT" == "$ROOT"/* ]]; then
  if ! git -C "$ROOT" check-ignore -q "$OUT/private/jwt-private.pem"; then
    echo "XATO: $OUT git tomonidan e'tiborsiz qoldirilmaydi — .gitignore ni tekshiring!" >&2
    exit 1
  fi
fi
