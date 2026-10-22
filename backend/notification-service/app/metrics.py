"""notification-service biznes metrikalari."""

from prometheus_client import Counter, Gauge

DELIVERIES = Counter("notifications_delivered_total", "Xabar natijalari", ["template", "result", "channel"])
BACKLOG = Gauge("notifications_backlog", "Vaqti kelgan, lekin yuborilmagan xabarlar")
TELEGRAM_UPDATES = Counter("telegram_updates_total", "Telegram webhook yangilanishlari", ["kind"])
