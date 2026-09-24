from rest_framework import serializers


class DoctorProfileSerializer(serializers.Serializer):
    """Onboarding profili — o'qish va qisman yangilash."""

    id = serializers.UUIDField(read_only=True)
    status = serializers.CharField(read_only=True)
    full_name = serializers.CharField(source="user.full_name", max_length=255, required=False)
    phone = serializers.CharField(source="user.phone", read_only=True)
    specialization_ids = serializers.ListField(child=serializers.UUIDField(), required=False, max_length=5)
    bio = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    experience_years = serializers.IntegerField(required=False, min_value=0, max_value=70)
    education = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    languages = serializers.ListField(
        child=serializers.ChoiceField(choices=["uz", "ru", "en", "kk", "tg", "tr"]), required=False, max_length=6
    )
    license_number = serializers.CharField(required=False, allow_blank=True, max_length=100)
    license_expires_at = serializers.DateField(required=False, allow_null=True)
    accepts_home_visits = serializers.BooleanField(required=False)
    home_visit_radius_km = serializers.IntegerField(required=False, min_value=1, max_value=100)

    def to_representation(self, doctor):
        data = super().to_representation(doctor)
        data["specialization_ids"] = [str(s) for s in doctor.specializations.values_list("id", flat=True)]
        return data

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if "user" in value:  # source="user.full_name" -> {"user": {"full_name": ..}}
            value["full_name"] = value.pop("user")["full_name"]
        return value


class DocumentUploadSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["diploma", "license", "certificate", "passport"])
    file = serializers.FileField()


class DocumentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    kind = serializers.CharField()
    original_name = serializers.CharField()
    content_type = serializers.CharField()
    size = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    download_url = serializers.SerializerMethodField()

    def get_download_url(self, doc):
        from django.urls import reverse

        from api.doctor.onboarding import sign_document

        path = reverse("doctor-document-download", kwargs={"token": sign_document(doc.id)})
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request else path


class OnboardingStatusSerializer(serializers.Serializer):
    status = serializers.CharField()
    reason = serializers.CharField()
    missing = serializers.ListField(child=serializers.CharField())
    submitted_at = serializers.DateTimeField(allow_null=True)
    sla_due_at = serializers.DateTimeField(allow_null=True)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000)


class ModerationDoctorSerializer(DoctorProfileSerializer):
    status_reason = serializers.CharField(read_only=True)
    submitted_at = serializers.DateTimeField(read_only=True)
    moderated_at = serializers.DateTimeField(read_only=True)
    sla_due_at = serializers.SerializerMethodField()
    documents = serializers.SerializerMethodField()

    def get_sla_due_at(self, doctor):
        from api.doctor.onboarding import MODERATION_SLA

        return (doctor.submitted_at + MODERATION_SLA).isoformat() if doctor.submitted_at else None

    def get_documents(self, doctor):
        return DocumentSerializer(doctor.documents.all(), many=True, context=self.context).data
