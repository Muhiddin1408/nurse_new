"""
booking/management/commands/expire_bookings.py — davriy ish (har daqiqa)

To'lanmagan bronlarni EXPIRED qiladi va muddati o'tgan hold'larni bo'shatadi.

    python manage.py expire_bookings

TARTIB MUHIM — avval bron, keyin slot:
    `expire_pending_bookings` bronlarni HELD holatidagi muddati o'tgan slotlar
    orqali topadi. Agar avval `release_expired_holds` ishlasa, slot FREE bo'lib
    qoladi va unga bog'langan bron abadiy PENDING_PAYMENT holatida qotib qoladi.
    `release_expired_holds` keyin faqat bronsiz qolgan hold'larni tozalaydi.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from api.booking.services import expire_pending_bookings
from api.locks import advisory_lock
from api.observability import metrics
from api.schedule.services import (
    EXPIRE_BATCH_SIZE,
    expired_hold_backlog,
    release_expired_holds,
)

MAX_BATCHES_PER_RUN = 50


class Command(BaseCommand):
    help = "Muddati o'tgan bronlar va slot hold'larini tozalaydi (har daqiqa)"

    def handle(self, *args, **options):
        bookings, slots = run_expiry()
        self.stdout.write(f"Bron expired: {bookings}, slot bo'shatildi: {slots}")


def run_expiry() -> tuple[int, int]:
    """`run_scheduler` ham shu funksiyani chaqiradi — tartib bitta joyda.

    Advisory lock (E10): boshqa nusxa ishlayotgan bo'lsa hech narsa qilmaydi.
    Adaptiv sikl (E9): partiya to'liq bo'lsa (backlog bor), 60 soniya
    kutmasdan darhol keyingi partiya olinadi.
    """
    with advisory_lock("expire_bookings") as acquired:
        if not acquired:
            return 0, 0

        bookings = slots = 0
        for _ in range(MAX_BATCHES_PER_RUN):
            b = expire_pending_bookings()
            s = release_expired_holds()
            bookings += b
            slots += s
            if b < EXPIRE_BATCH_SIZE and s < EXPIRE_BATCH_SIZE:
                break

        metrics.expire_backlog.set(expired_hold_backlog())
        return bookings, slots
