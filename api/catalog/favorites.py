"""
catalog/favorites.py — sevimli shifokorlar (C15.6).

    PUT    /api/v1/catalog/doctors/{id}/favorite    qo'shish (idempotent, 204)
    DELETE /api/v1/catalog/doctors/{id}/favorite    olib tashlash (idempotent, 204)
    GET    /api/v1/catalog/favorites                ro'yxat (oxirgi qo'shilgani birinchi)
"""

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.catalog import services as catalog_services
from api.catalog.serializers import DoctorListSerializer
from api.docs import TAG_CATALOG
from apps.account.models import FavoriteDoctor
from apps.catalog.models import Doctor

MAX_FAVORITES = 100


class FavoriteDoctorView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=[TAG_CATALOG], summary="Sevimlilarga qo'shish", request=None, responses={204: None, 404: None})
    def put(self, request, doctor_id):
        # A9 qoidasi: faqat tasdiqlangan shifokor — boshqasi mavjud emasdek
        if not Doctor.objects.filter(id=doctor_id, status=Doctor.Status.APPROVED, user__is_active=True).exists():
            return Response(status=status.HTTP_404_NOT_FOUND)
        if FavoriteDoctor.objects.filter(user=request.user).count() >= MAX_FAVORITES:
            return Response({"detail": f"Ko'pi bilan {MAX_FAVORITES} ta sevimli"}, status=status.HTTP_409_CONFLICT)
        FavoriteDoctor.objects.get_or_create(user=request.user, doctor_id=doctor_id)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(tags=[TAG_CATALOG], summary="Sevimlilardan olib tashlash", responses={204: None})
    def delete(self, request, doctor_id):
        FavoriteDoctor.objects.filter(user=request.user, doctor_id=doctor_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=[TAG_CATALOG], summary="Sevimli shifokorlar", responses=DoctorListSerializer(many=True))
class FavoriteListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        ids = list(
            FavoriteDoctor.objects.filter(user=request.user).order_by("-created_at").values_list("doctor_id", flat=True)
        )
        doctors = {d.id: d for d in catalog_services.get_doctors(ids)}
        # To'xtatilgan shifokor ro'yxatda qolmaydi (A9), lekin sevimli yozuvi
        # saqlanadi — qayta tasdiqlansa yana ko'rinadi
        return Response(DoctorListSerializer([doctors[i] for i in ids if i in doctors], many=True).data)
