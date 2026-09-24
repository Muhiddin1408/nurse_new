from rest_framework import serializers

from apps.account.models import Patient, Address


class PatientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = [
            "id",
            "full_name",
            "relation",
            "birth_date",
            "gender",
            "weight_kg",
        ]
        # `owner` maydoni serializerda YO'Q — uni request.user dan olamiz,
        # klient tanasidan emas. Aks holda mijoz boshqa odamning
        # profiliga o'zini "egasi" qilib yozib qo'yishi mumkin edi.



class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = [
            "id",
            "label",
            "city",
            "street",
            "entrance",
            "floor",
            "apartment",
            "comment",
            "latitude",
            "longitude",
            "is_default",
        ]