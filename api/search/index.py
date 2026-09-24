import logging

from elasticsearch import Elasticsearch, NotFoundError
logger = logging.getLogger(__name__)
DOCTOR_MAPPING = {
    "settings": {
        "analysis": {
            "analyzer": {
                # O'zbekcha/ruscha matn uchun. Ism qidiruvida "Юсуббаев" ni
                # "юсуббаев" bilan topish, qo'shimcha shovqinni tozalash uchun.
                "name_analyzer": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase"],
                }
            }
        }
    },
    "mappings": {
        "properties": {
            "id": {"type": "keyword"},
            "full_name": {"type": "text", "analyzer": "name_analyzer"},
            "specializations": {"type": "keyword"},  # aniq moslik uchun keyword
            "specialization_names": {"type": "text", "analyzer": "name_analyzer"},
            "experience_years": {"type": "integer"},
            "rating": {"type": "float"},
            "reviews_count": {"type": "integer"},
            "accepts_home_visits": {"type": "boolean"},
            "min_price": {"type": "long"},
            "clinic_ids": {"type": "keyword"},
            # ⬇ Geo-qidiruvning kaliti. Shifokorning uy chaqiruvi bazasi
            # (yoki klinikasi) joylashuvi. ES buni maxsus indekslaydi.
            "location": {"type": "geo_point"},
            "is_bookable": {"type": "boolean"},
        }
    },
}


def create_index(es: Elasticsearch) -> None:
    if not es.indices.exists(index=DOCTORS_INDEX):
        es.indices.create(index=DOCTORS_INDEX, body=DOCTOR_MAPPING)
        logger.info("Indeks yaratildi: %s", DOCTORS_INDEX)
DOCTORS_INDEX = "doctors_v1"