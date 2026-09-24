from rest_framework import serializers


class SpecializationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.SerializerMethodField()  # C14: tilga qarab
    slug = serializers.CharField()
    is_pediatric = serializers.BooleanField()
    accepts_children = serializers.BooleanField()
    min_patient_age = serializers.IntegerField(allow_null=True)
    max_patient_age = serializers.IntegerField(allow_null=True)
    allowed_gender = serializers.CharField()

    def get_name(self, obj) -> str:
        from api.i18n import localized

        return localized(obj, "name")


class ClinicSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    city = serializers.CharField()
    street = serializers.CharField()
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    rating = serializers.DecimalField(max_digits=3, decimal_places=2)
    reviews_count = serializers.IntegerField()


class DoctorListSerializer(serializers.Serializer):
    """Ro'yxat uchun QISQA ko'rinish. Detail'dan ataylab ajratilgan —

    ro'yxat 20+ shifokorni bitta so'rovda qaytaradi, shuning uchun har bir
    elementni yengil tutish kerak. Bio, litsenziya kabi og'ir maydonlar
    faqat DoctorDetailSerializer'da.
    """

    id = serializers.UUIDField()
    full_name = serializers.CharField()
    experience_years = serializers.IntegerField()
    # C11: < 5 ta sharhda reyting null — ilova "Yangi shifokor" ko'rsatadi
    rating = serializers.SerializerMethodField()
    reviews_count = serializers.IntegerField()
    is_new = serializers.SerializerMethodField()
    specializations = serializers.ListField(child=serializers.CharField())
    accepts_home_visits = serializers.BooleanField()
    distance_km = serializers.FloatField(allow_null=True, required=False)  # C15.5

    def get_rating(self, obj) -> float | None:
        from api.booking.reviews import display_rating

        shown = display_rating(obj.rating, obj.reviews_count)
        return None if shown is None else f"{shown:.2f}"

    def get_is_new(self, obj) -> bool:
        from api.booking.reviews import MIN_REVIEWS_FOR_RATING

        return obj.reviews_count < MIN_REVIEWS_FOR_RATING


class DoctorDetailSerializer(DoctorListSerializer):
    """Shifokor kartochkasi. Ro'yxatdagi maydonlar + og'irlari."""

    bio = serializers.CharField()
    license_number = serializers.CharField()
    home_visit_radius_km = serializers.IntegerField()
    clinics = ClinicSerializer(many=True)


class ServiceSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.SerializerMethodField()  # C14
    place = serializers.CharField()
    price = serializers.DecimalField(max_digits=12, decimal_places=2)
    duration_minutes = serializers.IntegerField()

    def get_name(self, obj) -> str:
        from api.i18n import localized

        return localized(obj, "name")
