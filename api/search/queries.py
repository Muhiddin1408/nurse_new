from api.search.index import DOCTORS_INDEX
from api.search.utils import _es_client


def search_doctors(
        *,
        text: str | None = None,
        specialization_id: str | None = None,
        home_visits_only: bool = False,
        near_lat: float | None = None,
        near_lon: float | None = None,
        max_distance_km: int = 15,
        min_rating: float | None = None,
        sort: str = "relevance",
        limit: int = 20,
        offset: int = 0,
) -> dict:
    """Bu funksiya catalog_services.list_doctors ni ALMASHTIRADI — o'qish
    yo'li endi ES orqali.

    E'tibor bering: filter (aniq moslik) va must (ballga ta'sir qiladi)
    ajratilgan. Bu ES'da muhim tafovut: `filter` keshlanadi va ball
    hisoblamaydi (tez), `must` esa relevantlikka ta'sir qiladi.
    """
    es = _es_client()

    filters: list[dict] = [{"term": {"is_bookable": True}}]
    must: list[dict] = []

    if specialization_id:
        filters.append({"term": {"specializations": specialization_id}})
    if home_visits_only:
        filters.append({"term": {"accepts_home_visits": True}})
    if min_rating is not None:
        filters.append({"range": {"rating": {"gte": min_rating}}})

    if near_lat is not None and near_lon is not None:
        filters.append(
            {
                "geo_distance": {
                    "distance": f"{max_distance_km}km",
                    "location": {"lat": near_lat, "lon": near_lon},
                }
            }
        )

    if text:
        must.append(
            {
                "multi_match": {
                    "query": text,
                    "fields": ["full_name^3", "specialization_names"],  # ^3 = ismga ko'proq vazn
                    "fuzziness": "AUTO",  # imlo xatosini kechiradi: "гастролог" -> "гастроэнтеролог"
                }
            }
        )

    query = {"bool": {"filter": filters, "must": must or [{"match_all": {}}]}}

    body = {
        "query": query,
        "from": offset,
        "size": limit,
        "sort": _build_sort(sort, near_lat, near_lon),
    }

    response = es.search(index=DOCTORS_INDEX, body=body)

    return {
        "total": response["hits"]["total"]["value"],
        "results": [hit["_source"] for hit in response["hits"]["hits"]],
    }


def _build_sort(sort: str, lat: float | None, lon: float | None) -> list:
    if sort == "distance" and lat is not None and lon is not None:
        return [{"_geo_distance": {"location": {"lat": lat, "lon": lon}, "order": "asc"}}]
    if sort == "rating":
        return [{"rating": "desc"}, {"reviews_count": "desc"}]
    if sort == "price":
        return [{"min_price": "asc"}]
    return ["_score", {"rating": "desc"}]  # relevance

