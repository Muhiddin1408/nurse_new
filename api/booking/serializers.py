from rest_framework import serializers

# SlotSerializer api/schedule/serializers.py ga ko'chirildi — slot jadval
# modulining tushunchasi, bron unga egalik qilmaydi.


class BookingItemSerializer(serializers.Serializer):
    service_name = serializers.CharField()
    price = serializers.DecimalField(max_digits=12, decimal_places=2)
    duration_minutes = serializers.IntegerField()


class BookingSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    number = serializers.CharField()
    status = serializers.CharField()
    status_display = serializers.CharField(source="get_status_display")
    total_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    # C12: chegirma ko'rinsin — mijoz nima uchun kamroq to'layotganini bilsin
    original_price = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    discount_kind = serializers.CharField()
    promo_code = serializers.CharField()
    created_at = serializers.DateTimeField()
    items = BookingItemSerializer(many=True)


class CreateBookingSerializer(serializers.Serializer):
    """Kiruvchi so'rov.

    DIQQAT: bu yerda faqat FORMAT tekshiriladi (UUID'mi, ro'yxat bo'shmasmi).
    BIZNES tekshiruvlari — xizmat shifokorga tegishlimi, uy chaqiruviga manzil
    berilganmi — services.py da. Ularni bu yerga ko'chirmang, aks holda
    gRPC ga o'tganda hammasi yo'qoladi.
    """

    patient_id = serializers.UUIDField()
    doctor_id = serializers.UUIDField()
    slot_id = serializers.UUIDField()
    service_ids = serializers.ListField(
        child=serializers.UUIDField(), min_length=1, max_length=10
    )
    address_id = serializers.UUIDField(required=False, allow_null=True)
    comment = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    promo_code = serializers.CharField(required=False, allow_blank=True, max_length=32, default="")  # C12


class CompleteBookingSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class NoShowSerializer(serializers.Serializer):
    absent = serializers.ChoiceField(choices=["client", "doctor"])
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class OpenDisputeSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(choices=["doctor_no_show", "poor_service", "wrong_charge", "other"])
    description = serializers.CharField(required=False, allow_blank=True, max_length=4000)


class ResolveDisputeSerializer(serializers.Serializer):
    in_favor_of = serializers.ChoiceField(choices=["client", "doctor"])
    note = serializers.CharField(max_length=4000)
    refund_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0, min_value=0)


class DisputeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    booking_id = serializers.UUIDField()
    reason = serializers.CharField()
    description = serializers.CharField()
    status = serializers.CharField()
    due_at = serializers.DateTimeField()
    resolution_note = serializers.CharField()
    refund_id = serializers.UUIDField(allow_null=True)
    created_at = serializers.DateTimeField()


class CancellationPreviewSerializer(serializers.Serializer):
    allowed = serializers.BooleanField()
    refund_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    penalty_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    reason_code = serializers.CharField()


class CancelBookingSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)


class WaitlistJoinSerializer(serializers.Serializer):
    """C10. `doctor_id` — `UUIDField` (A4): moslik tekshiruvi servisda."""

    doctor_id = serializers.UUIDField()
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    place = serializers.ChoiceField(choices=["clinic", "home"], default="clinic")


class WaitlistEntrySerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    doctor_id = serializers.UUIDField(read_only=True)
    doctor_name = serializers.CharField(source="doctor.user.full_name", read_only=True)
    date_from = serializers.DateField(read_only=True)
    date_to = serializers.DateField(read_only=True)
    place = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class RescheduleBookingSerializer(serializers.Serializer):
    """C3. `UUIDField` — `PrimaryKeyRelatedField` EMAS (A4 qoidasi): egalik va
    moslik tekshiruvi servisda, global queryset bilan begona slot qabul
    qilinmaydi."""

    new_slot_id = serializers.UUIDField()
