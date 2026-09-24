from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.account.models import Patient, Address
from api.docs import TAG_PATIENTS
from api.patients.serializers import PatientSerializer, AddressSerializer
from api.patients.utils import _clear_other_defaults

def _soft_destroy(obj, *, field: str):
    from apps.booking.models import Booking

    bookings = Booking.objects.filter(**{field: obj})
    if bookings.filter(status__in=Booking.ACTIVE_STATUSES).exists():
        return Response({"detail": "Faol bronlari bor — avval ularni bekor qiling"}, status=status.HTTP_409_CONFLICT)
    if bookings.exists():
        type(obj).objects.filter(pk=obj.pk).update(is_deleted=True, **({"is_default": False} if field == "address" else {}))
    else:
        obj.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


_TAGGED = {action: extend_schema(tags=[TAG_PATIENTS]) for action in
           ("list", "create", "retrieve", "update", "partial_update", "destroy")}


@extend_schema_view(**_TAGGED)
class PatientViewSet(viewsets.ModelViewSet):
    """
    GET    /api/v1/patients
    POST   /api/v1/patients
    GET    /api/v1/patients/{id}
    PATCH  /api/v1/patients/{id}
    DELETE /api/v1/patients/{id}
    """

    serializer_class = PatientSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # ⚠️ ENG MUHIM QATOR SHU FAYLDA.
        # Faqat so'rov yuborgan foydalanuvchining bemorlarini qaytaramiz.
        # Buni unutish — boshqa mijozning bemor ro'yxatini ID orqali
        # ko'rsatib qo'yadigan klassik IDOR zaifligi (OWASP Top 10).
        if getattr(self, "swagger_fake_view", False):  # Swagger sxema generatsiyasi — user yo'q
            return Patient.objects.none()
        return Patient.objects.filter(owner=self.request.user, is_deleted=False).order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    def destroy(self, request, *args, **kwargs):
        """C15.2: bronlari bor bemor — soft-delete, yo'q bo'lsa — haqiqiy DELETE.
        Faol (to'lov kutayotgan / tasdiqlangan) broni bo'lsa — 409: avval bekor qilinsin."""
        return _soft_destroy(self.get_object(), field="patient")


@extend_schema_view(**_TAGGED)
class AddressViewSet(viewsets.ModelViewSet):
    """
    GET    /api/v1/addresses
    POST   /api/v1/addresses
    PATCH  /api/v1/addresses/{id}
    DELETE /api/v1/addresses/{id}
    """

    serializer_class = AddressSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Address.objects.none()
        return Address.objects.filter(user=self.request.user, is_deleted=False).order_by("-is_default", "-created_at")

    def destroy(self, request, *args, **kwargs):
        return _soft_destroy(self.get_object(), field="address")

    def perform_create(self, serializer):
        address = serializer.save(user=self.request.user)
        if address.is_default:
            _clear_other_defaults(self.request.user, keep=address.id)

    def perform_update(self, serializer):
        address = serializer.save()
        if address.is_default:
            _clear_other_defaults(self.request.user, keep=address.id)
