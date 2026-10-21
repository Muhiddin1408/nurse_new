"""admin-service biznes metrikalari."""

from prometheus_client import Counter

ADMIN_LOGINS = Counter("admin_logins_total", "Admin kirish urinishlari", ["result"])
AUDIT_RECORDED = Counter("audit_records_total", "Audit jurnaliga yozilgan yozuvlar", ["producer"])
SETTINGS_CHANGED = Counter("admin_settings_changed_total", "Global sozlamalar o'zgarishi")
