"""auth-service biznes metrikalari."""

from prometheus_client import Counter

OTP_REQUESTS = Counter("otp_requests_total", "OTP so'rovlari", ["result"])
OTP_VERIFY = Counter("otp_verify_total", "OTP tekshiruvlari", ["result"])
TOKENS_ISSUED = Counter("auth_tokens_issued_total", "Chiqarilgan token juftliklari", ["grant"])
REFRESH_REUSE = Counter("refresh_reuse_detected_total", "Rotatsiya qilingan refresh token qayta ishlatildi")
