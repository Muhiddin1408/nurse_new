from rest_framework import serializers


class SlotSerializer(serializers.Serializer):
    """Bo'sh vaqt. Faqat kerakli maydonlar — bu javob eng ko'p uzatiladi,
    shuning uchun uni yengil tutish kerak.

    Bu serializer `schedule` modulida, `booking` da emas: slot — jadval
    modulining tushunchasi. Bron uni faqat ISHLATADI, unga EGALIK qilmaydi.
    Faza 4 da jadval alohida servisga chiqqanda, javob shakli o'sha servis
    bilan birga ko'chadi.

    ⚠️ Maydon qo'shishdan oldin ikki marta o'ylang: `status`, `doctor_id` —
    klientga kerak emas (u allaqachon qaysi shifokorni so'raganini biladi, va
    faqat FREE slotlar qaytadi). Har bir ortiqcha maydon 200 ta slotga
    ko'paytiriladi.

    `clinic_id` esa KERAK: bir shifokorning klinika va uy slotlari aralash
    keladi, klient xizmat joyiga (`place`) mos slotni ko'rsatishi uchun
    null = uy chaqiruvi, aks holda qaysi klinika ekanini bilishi shart.
    """

    id = serializers.UUIDField()
    start_at = serializers.DateTimeField()
    end_at = serializers.DateTimeField()
    clinic_id = serializers.UUIDField(allow_null=True)