from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import Specialization
from api.catalog.serializers import (
    ClinicSerializer,
    DoctorDetailSerializer,
    DoctorListSerializer,
    ServiceSerializer,
    SpecializationSerializer,
)
from api.catalog.utils import _bool_param, _int_param
from api.catalog import services as catalog_services
from api.docs import DETAIL, TAG_CATALOG

_PAGINATION = [
    OpenApiParameter("limit", OpenApiTypes.INT, description="Default 20, max 50"),
    OpenApiParameter("offset", OpenApiTypes.INT, description="Default 0"),
]


@extend_schema(tags=[TAG_CATALOG], summary="Mutaxassisliklar", responses=SpecializationSerializer(many=True), auth=[])
class SpecializationListView(APIView):
    """GET /api/v1/specializations

    Asosiy sahifadagi "Специалисты" bo'limi ostidagi filtrlar.
    Kamdan-kam o'zgaradi -> agressiv keshlash mumkin (Faza 6).
    """

    permission_classes = []

    def get(self, request: Request):
        qs = Specialization.objects.all().order_by("name")
        return Response(SpecializationSerializer(qs, many=True).data)


@extend_schema(
    tags=[TAG_CATALOG],
    summary="Faol klinikalar",
    parameters=[OpenApiParameter("city", OpenApiTypes.STR), *_PAGINATION],
    responses=ClinicSerializer(many=True),
    auth=[],
)
class ClinicListView(APIView):
    """GET /api/v1/clinics?city="""

    permission_classes = []

    def get(self, request: Request):
        clinics = catalog_services.list_clinics(
            city=request.query_params.get("city"),
            limit=_int_param(request, "limit", default=20, max_value=50),
            offset=_int_param(request, "offset", default=0),
        )
        return Response(ClinicSerializer(clinics, many=True).data)


@extend_schema(
    tags=[TAG_CATALOG],
    summary="Shifokorlar ro'yxati",
    parameters=[
        OpenApiParameter("specialization", OpenApiTypes.UUID),
        OpenApiParameter("clinic", OpenApiTypes.UUID),
        OpenApiParameter("home_only", OpenApiTypes.BOOL, description="Faqat uy chaqiruvi qabul qiladiganlar"),
        OpenApiParameter("lat", OpenApiTypes.NUMBER, description="home_only bilan: faqat shu nuqtaga yetib boradiganlar"),
        OpenApiParameter("lng", OpenApiTypes.NUMBER),
        OpenApiParameter("radius", OpenApiTypes.NUMBER,
                         description="km, default 5, max 50. home_only'siz: klinikasi shu radiusdagi shifokorlar, "
                                     "eng yaqini birinchi (javobda distance_km)"),
        *_PAGINATION,
    ],
    responses=DoctorListSerializer(many=True),
    auth=[],
)
class DoctorListView(APIView):
    """GET /api/v1/doctors?specialization=&clinic=&home_only=&limit=&offset=

    Dizayndagi "Гастроэнтеролог" ro'yxat ekrani.
    """

    permission_classes = []

    def get(self, request: Request):
        try:
            lat = _coord(request, "lat", 90)
            lng = _coord(request, "lng", 180)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        if (lat is None) != (lng is None):
            return Response({"detail": "lat va lng birga beriladi"}, status=400)
        try:
            radius = float(request.query_params.get("radius") or 5)
        except ValueError:
            return Response({"detail": "radius noto'g'ri"}, status=400)
        if not 0 < radius <= 50:
            return Response({"detail": "radius: 0 < radius <= 50"}, status=400)
        doctors = catalog_services.list_doctors(
            lat=lat,
            lng=lng,
            radius_km=radius,
            specialization_id=request.query_params.get("specialization") or None,
            clinic_id=request.query_params.get("clinic") or None,
            home_visits_only=_bool_param(request, "home_only"),
            limit=_int_param(request, "limit", default=20, max_value=50),
            offset=_int_param(request, "offset", default=0),
        )
        return Response(DoctorListSerializer(doctors, many=True).data)


def _coord(request: Request, name: str, limit: float) -> float | None:
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} noto'g'ri") from None
    if not -limit <= value <= limit:
        raise ValueError(f"{name} noto'g'ri")
    return value


class DoctorDetailView(APIView):
    """GET /api/v1/doctors/{id}

    Shifokor kartochkasi — "Запись / О враче / Отзывы" tab'lari uchun asos.
    Отзывы alohida endpoint bo'ladi (Review modeli hali yozilmagan — keyingi
    faylda).
    """

    permission_classes = []

    @extend_schema(
        tags=[TAG_CATALOG],
        summary="Shifokor kartochkasi",
        operation_id="catalog_doctor_detail",  # ro'yxat bilan to'qnashmasin
        responses={200: DoctorDetailSerializer, 404: None},
        auth=[],
    )
    def get(self, request: Request, doctor_id):
        doctor = catalog_services.get_doctor_detail(doctor_id)
        if doctor is None:
            return Response(status=404)
        return Response(DoctorDetailSerializer(doctor).data)


@extend_schema(
    tags=[TAG_CATALOG],
    summary="Shifokor xizmatlari",
    parameters=[OpenApiParameter("place", OpenApiTypes.STR, enum=["clinic", "home"])],
    responses={200: ServiceSerializer(many=True), 400: DETAIL},
    auth=[],
)
class DoctorServicesView(APIView):
    """GET /api/v1/doctors/{id}/services?place=home

    Bron oynasidagi "Шаг 1. Выбор услуги".
    `place` bo'yicha filtrlash muhim: klinika va uy xizmatlari bitta bronda
    aralashmasligi kerak (booking/services.py dagi tekshiruv shuni talab qiladi) —
    shuning uchun frontend qaysi rejimda ekanini bilib, shunga mos ro'yxat
    so'rashi kerak.
    """

    permission_classes = []

    def get(self, request: Request, doctor_id):
        place = request.query_params.get("place")
        if place not in (None, "clinic", "home"):
            return Response({"detail": "place: 'clinic' yoki 'home' bo'lishi kerak"}, status=400)

        services = catalog_services.list_doctor_services(doctor_id, place=place)
        return Response(ServiceSerializer(services, many=True).data)



# ---------------------------------------------------------------------------
# C15.4: qidiruv
# ---------------------------------------------------------------------------


class DoctorSearchResponseSerializer(serializers.Serializer):
    engine = serializers.CharField(help_text="elasticsearch | postgres (degradatsiya)")
    total = serializers.IntegerField(allow_null=True, help_text="postgres rejimida null")
    results = DoctorListSerializer(many=True)


@extend_schema(
    tags=[TAG_CATALOG],
    summary="Shifokor qidiruvi (C15.4)",
    description=(
        "Matn, mutaxassislik, masofa va reyting bo'yicha qidiruv. Elasticsearch "
        "javob bermasa so'rov avtomat Postgres'ga tushadi — javobdagi `engine` "
        "shuni aytadi (`postgres` = imlo xatosi kechirilmaydi, `total` null)."
    ),
    parameters=[
        OpenApiParameter("q", OpenApiTypes.STR, description="Ism yoki mutaxassislik"),
        OpenApiParameter("specialization", OpenApiTypes.UUID),
        OpenApiParameter("home_only", OpenApiTypes.BOOL),
        OpenApiParameter("lat", OpenApiTypes.NUMBER),
        OpenApiParameter("lng", OpenApiTypes.NUMBER),
        OpenApiParameter("radius", OpenApiTypes.NUMBER, description="km, default 15, max 50"),
        OpenApiParameter("min_rating", OpenApiTypes.NUMBER, description="0–5"),
        OpenApiParameter(
            "sort", OpenApiTypes.STR,
            description="relevance (default) | rating | distance | price",
        ),
        *_PAGINATION,
    ],
    responses={200: DoctorSearchResponseSerializer, 400: DETAIL},
    auth=[],
)
class DoctorSearchView(APIView):
    """GET /api/v1/catalog/search"""

    permission_classes = []

    SORTS = ("relevance", "rating", "distance", "price")

    def get(self, request: Request):
        from api.search import service as search_service

        try:
            lat = _coord(request, "lat", 90)
            lng = _coord(request, "lng", 180)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        if (lat is None) != (lng is None):
            return Response({"detail": "lat va lng birga beriladi"}, status=400)

        try:
            radius = float(request.query_params.get("radius") or 15)
        except ValueError:
            return Response({"detail": "radius noto'g'ri"}, status=400)
        if not 0 < radius <= 50:
            return Response({"detail": "radius: 0 < radius <= 50"}, status=400)

        min_rating = request.query_params.get("min_rating")
        if min_rating not in (None, ""):
            try:
                min_rating = float(min_rating)
            except ValueError:
                return Response({"detail": "min_rating noto'g'ri"}, status=400)
            if not 0 <= min_rating <= 5:
                return Response({"detail": "min_rating: 0 <= min_rating <= 5"}, status=400)
        else:
            min_rating = None

        sort = request.query_params.get("sort") or "relevance"
        if sort not in self.SORTS:
            return Response({"detail": "sort noto'g'ri"}, status=400)
        # `distance` saralashi koordinatasiz ma'nosiz — jim `relevance` ga
        # o'tib ketsa, ilova "eng yaqini birinchi" deb noto'g'ri ko'rsatardi
        if sort == "distance" and lat is None:
            return Response({"detail": "sort=distance uchun lat va lng kerak"}, status=400)

        result = search_service.search(
            text=(request.query_params.get("q") or "").strip(),
            specialization_id=request.query_params.get("specialization") or None,
            home_visits_only=_bool_param(request, "home_only"),
            lat=lat, lng=lng, radius_km=radius,
            min_rating=min_rating, sort=sort,
            limit=_int_param(request, "limit", default=20, max_value=50),
            offset=_int_param(request, "offset", default=0),
        )
        return Response({
            "engine": result.engine,
            "total": result.total,
            "results": DoctorListSerializer(result.doctors, many=True).data,
        })
