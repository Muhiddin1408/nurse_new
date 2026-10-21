#!/bin/sh
# S3-mos obyekt ombori (SeaweedFS, ADR-0010). Sirlar FAQAT muhitdan — s3.json konteyner ichida
# har ishga tushishda generatsiya qilinadi va diskka (volume'ga) yozilmaydi.
#
#   S3_ADMIN_ACCESS_KEY / S3_ADMIN_SECRET_KEY   — bootstrap va ops (barcha bucketlar)
#   S3_FILE_SERVICE_ACCESS_KEY / ..._SECRET_KEY — file-service: faqat ikki bucket, admin huquqisiz
#   public-media — anonim O'QISH (avatar, shifokor rasmi); private-documents — faqat presigned URL.
set -eu

# Root'dan imtiyozsiz `seaweed` foydalanuvchisiga o'tamiz (image'ning o'z entrypoint'i kabi).
if [ "$(id -u)" = "0" ]; then
  chown -R seaweed:seaweed /data
  exec su-exec seaweed "$0" "$@"
fi

: "${S3_ADMIN_ACCESS_KEY:?S3_ADMIN_ACCESS_KEY majburiy}"
: "${S3_ADMIN_SECRET_KEY:?S3_ADMIN_SECRET_KEY majburiy}"
: "${S3_FILE_SERVICE_ACCESS_KEY:?S3_FILE_SERVICE_ACCESS_KEY majburiy}"
: "${S3_FILE_SERVICE_SECRET_KEY:?S3_FILE_SERVICE_SECRET_KEY majburiy}"

PUBLIC_BUCKET="${S3_PUBLIC_BUCKET:-public-media}"
PRIVATE_BUCKET="${S3_PRIVATE_BUCKET:-private-documents}"
CONFIG=/tmp/s3.json
umask 077
cat > "$CONFIG" <<JSON
{
  "identities": [
    {
      "name": "admin",
      "credentials": [{"accessKey": "${S3_ADMIN_ACCESS_KEY}", "secretKey": "${S3_ADMIN_SECRET_KEY}"}],
      "actions": ["Admin", "Read", "List", "Tagging", "Write"]
    },
    {
      "name": "file-service",
      "credentials": [{"accessKey": "${S3_FILE_SERVICE_ACCESS_KEY}", "secretKey": "${S3_FILE_SERVICE_SECRET_KEY}"}],
      "actions": [
        "Read:${PUBLIC_BUCKET}", "Write:${PUBLIC_BUCKET}", "List:${PUBLIC_BUCKET}", "Tagging:${PUBLIC_BUCKET}",
        "Read:${PRIVATE_BUCKET}", "Write:${PRIVATE_BUCKET}", "List:${PRIVATE_BUCKET}", "Tagging:${PRIVATE_BUCKET}"
      ]
    },
    {
      "name": "anonymous",
      "actions": ["Read:${PUBLIC_BUCKET}"]
    }
  ]
}
JSON

weed server -dir=/data -ip=127.0.0.1 -ip.bind=0.0.0.0 -master.volumeSizeLimitMB=1024 -volume.max=0 \
  -s3 -s3.port=8333 -s3.config="$CONFIG" &
WEED_PID=$!
trap 'kill -TERM "$WEED_PID" 2>/dev/null; wait "$WEED_PID"' TERM INT

# Bucketlar idempotent yaratiladi (bor bo'lsa — o'tkazib yuboriladi).
for _ in $(seq 1 60); do
  if echo "s3.bucket.list" | weed shell -master=127.0.0.1:9333 >/tmp/buckets 2>/dev/null; then
    break
  fi
  sleep 1
done
for bucket in "$PUBLIC_BUCKET" "$PRIVATE_BUCKET"; do
  if ! grep -qw "${bucket}" /tmp/buckets; then
    echo "s3.bucket.create -name ${bucket}" | weed shell -master=127.0.0.1:9333
  fi
done
touch /tmp/ready
wait "$WEED_PID"
