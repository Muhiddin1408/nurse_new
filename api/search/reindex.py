import logging

from api.search.index import create_index, DOCTORS_INDEX
from api.search.utils import _es_client

logger = logging.getLogger(__name__)
def reindex_all() -> int:
    """PostgreSQL'dan butun ES'ni qayta quradi.

    Qachon kerak: ES o'lganda, mapping o'zgarganda, yoki loyihaga qidiruv
    keyin qo'shilganda (mavjud ma'lumotni bir marta yuklash uchun).

    Bu funksiya CQRS'ning eng katta afzalligini isbotlaydi: o'qish modeli
    to'liq QAYTA TIKLANUVCHI, chunki haqiqat manbai boshqa joyda.

    management/commands/reindex_doctors.py ichida chaqiriladi.
    """
    from apps.catalog.models import Doctor
    from elasticsearch.helpers import bulk

    es = _es_client()
    create_index(es)

    doctors = (
        Doctor.objects.filter(status=Doctor.Status.APPROVED)
        .select_related("user")
        .prefetch_related("specializations", "services", "affiliations__clinic")
    )

    def generate_actions():
        for doctor in doctors.iterator(chunk_size=500):
            yield {
                "_index": DOCTORS_INDEX,
                "_id": str(doctor.id),
                "_source": _doctor_to_doc(doctor),
            }

    success, _ = bulk(es, generate_actions())
    logger.info("Reindex tugadi: %s shifokor", success)
    return success


def _doctor_to_doc(doctor) -> dict:
    """ORM obyektini ES hujjatiga. Reindex uchun — projector data'dan quradi."""
    services = list(doctor.services.filter(is_active=True))
    min_price = min((s.price for s in services), default=0)

    # Joylashuv: birinchi faol klinika yoki (uy chaqiruvchi bo'lsa) keyinroq
    # shifokorning bazaviy hududi. Soddalik uchun birinchi klinika.
    location = None
    for aff in doctor.affiliations.all():
        if aff.is_active and aff.clinic:
            location = {"lat": float(aff.clinic.latitude), "lon": float(aff.clinic.longitude)}
            break

    return {
        "id": str(doctor.id),
        "full_name": doctor.user.full_name,
        "specializations": [str(s.id) for s in doctor.specializations.all()],
        "specialization_names": [s.name for s in doctor.specializations.all()],
        "experience_years": doctor.experience_years,
        "rating": float(doctor.rating),
        "reviews_count": doctor.reviews_count,
        "accepts_home_visits": doctor.accepts_home_visits,
        "min_price": int(min_price),
        "clinic_ids": [str(a.clinic_id) for a in doctor.affiliations.all() if a.is_active],
        "location": location,
        "is_bookable": True,
    }
