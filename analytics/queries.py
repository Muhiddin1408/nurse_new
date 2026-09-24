# Bu so'rovlar millionlab qatorni skanerlaydi, lekin ClickHouse'da
# millisekundlarda ishlaydi — chunki ustunli saqlash va LowCardinality.
# XUDDI SHU so'rovlarni PostgreSQL'da ishlatsangiz, sekundlar ketardi va
# production bazasini bloklardi.
#
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  BITTA BRON = BIR NECHA QATOR.                                           ║
# ║  Har bir holat o'zgarishi alohida hodisa, va har bir hodisa alohida      ║
# ║  qator: `pending_payment` (yaratildi) -> `confirmed` yoki                 ║
# ║  `cancelled`/`expired`.                                                   ║
# ║                                                                           ║
# ║  Shuning uchun FILTRSIZ `count()` — BRONLAR soni EMAS, hodisalar soni.   ║
# ║  Har bir so'rov o'zi qaysi holatlarni sanayotganini ANIQ aytishi kerak.  ║
# ║  Buni unutish eng oson yo'l bilan sizni ikki barobar xato raqamga        ║
# ║  olib keladi — va grafik ishonchli ko'rinib turaveradi.                   ║
# ╚══════════════════════════════════════════════════════════════════════════╝

QUERIES = {
    # 1. Bekor qilish darajasi — mutaxassislik kesimida.
    #    saga_compensations metrikasi "nechta" desa, bu "QAYSI SOHADA" deydi.
    #    ⚠️ `status IN` filtri MAJBURIY: usiz `pending_payment` qatorlari
    #    maxrajga qo'shilib, bekor qilish darajasini ikki barobar PASAYTIRARDI.
    "cancellation_rate_by_specialization": """
                                           SELECT specialization,
                                                  count()                                     AS total,
                                                  sum(is_cancelled)                           AS cancelled,
                                                  round(sum(is_cancelled) / count() * 100, 1) AS cancel_rate_pct
                                           FROM fact_booking FINAL  -- E4: takrorlangan hodisalar birlashtiriladi
                                           WHERE created_date >= today() - 90
                                             AND status IN ('confirmed', 'cancelled', 'expired')  -- yakunlangan holatlar (pending_payment — oraliq)
                                           GROUP BY specialization
                                           ORDER BY cancel_rate_pct DESC
                                           """,

    # 2. Kunlik daromad tendensiyasi — faqat tasdiqlangan bronlar
    "daily_revenue": """
                     SELECT created_date,
                            sum(total_price) AS revenue,
                            count()          AS bookings
                     FROM fact_booking FINAL  -- E4: takrorlangan hodisalar birlashtiriladi
                     WHERE status = 'confirmed'
                       AND created_date >= today() - 30
                     GROUP BY created_date
                     ORDER BY created_date
                     """,

    # 3. Uy chaqiruvi vs klinika — hudud kesimida
    #    Faqat TASDIQLANGAN bronlar: "qayerda qabul bo'ldi" degan savolga
    #    javob beradi. Filtrsiz bitta bron uch marta sanalardi.
    "home_vs_clinic_by_city": """
                              SELECT city,
                                     sum(is_home_visit)           AS home_visits,
                                     count() - sum(is_home_visit) AS clinic_visits
                              FROM fact_booking FINAL  -- E4: takrorlangan hodisalar birlashtiriladi
                              WHERE created_date >= today() - 30
                                AND status = 'confirmed'
                              GROUP BY city
                              ORDER BY count() DESC LIMIT 20
                              """,

    # 4. Konversiya voronkasi: yaratilgan -> tasdiqlangan
    #    (to'lov integratsiyasi qanchalik yaxshi ishlayotganini ko'rsatadi)
    #
    #    `created` — MAXRAJ, `BookingCreated` hodisasidan keladi. Usiz
    #    voronka boshlanishi ko'rinmasdi: faqat tugagan bronlar sanalardi,
    #    ya'ni "boshlandi, lekin to'lanmadi" holati statistikada yo'q edi.
    #
    #    `created != confirmed + cancelled + expired` bo'lishi NORMAL —
    #    ayirma hozir to'lov kutayotgan bronlar (hali tugamagan).
    "conversion_funnel": """
                         SELECT toStartOfWeek(created_date)         AS week,
                                countIf(status = 'pending_payment') AS created,
                                countIf(status = 'confirmed')       AS confirmed,
                                countIf(status = 'cancelled')       AS cancelled,
                                countIf(status = 'expired')         AS expired,
                                round(countIf(status = 'confirmed')
                                    / nullIf(countIf(status = 'pending_payment'), 0) * 100,
                                      1)                            AS conversion_pct
                         FROM fact_booking FINAL  -- E4: takrorlangan hodisalar birlashtiriladi
                         WHERE created_date >= today() - 90
                         GROUP BY week
                         ORDER BY week
                         """,
    # ⬆ `nullIf(..., 0)` — nolga bo'linishdan himoya. Usiz ma'lumot yo'q
    # haftada so'rov `inf` yoki `nan` qaytarib, grafikni buzardi.
}


# ===========================================================================
# E14: AGREGATDAN O'QIYDIGAN SO'ROVLAR
#
# Yuqoridagi `QUERIES` xom `fact_booking` ni skanerlaydi — ular aniq, lekin
# ma'lumot o'sgani sayin qimmatlashadi. Uzoq oynali dashboard panellari
# (oylar, yillar) `daily_booking_agg` dan o'qishi kerak: u kuniga bir marta
# `FINAL` bilan qayta hisoblanadi (`analytics/aggregates.py`), ya'ni
# takrorlangan hodisalar allaqachon birlashtirilgan.
#
# ⚠️ Agregatda ham `FINAL` kerak: jadval ReplacingMergeTree, va qayta
# hisoblash eski qatorni darhol emas, fonda almashtiradi. Usiz bitta kun
# ikki marta sanalishi mumkin — aynan biz qochmoqchi bo'lgan xato.
#
# ⚠️ `unique_clients` ni kunlar bo'ylab QO'SHIB BO'LMAYDI: bir mijoz ikki
# kunda kelsa, kunlik yig'indi uni ikki marta sanaydi. Shuning uchun u faqat
# kunlik panelda ishlatiladi; oylik "noyob mijoz" kerak bo'lsa — xom
# jadvaldan `uniqExact(client_hash)`.
# ===========================================================================

AGG_QUERIES = {
    # 1. Uzoq oynali daromad tendensiyasi (bir yil) — dashboardning asosiy grafigi
    "revenue_trend_long": """
                          SELECT created_date,
                                 sum(revenue)  AS revenue,
                                 sum(bookings) AS bookings
                          FROM daily_booking_agg FINAL
                          WHERE status = 'confirmed'
                            AND created_date >= today() - 365
                          GROUP BY created_date
                          ORDER BY created_date
                          """,

    # 2. Mutaxassislik bo'yicha oylik bekor qilish darajasi
    #    `status IN` filtri — xom so'rovlardagi bilan bir xil sabab:
    #    `pending_payment` oraliq holat, maxrajga kirmaydi.
    "cancellation_rate_monthly": """
                                 SELECT toStartOfMonth(created_date)                    AS month,
                                        specialization,
                                        sum(bookings)                                   AS total,
                                        sum(cancelled)                                  AS cancelled,
                                        round(sum(cancelled) / sum(bookings) * 100, 1)  AS cancel_rate_pct
                                 FROM daily_booking_agg FINAL
                                 WHERE created_date >= today() - 365
                                   AND status IN ('confirmed', 'cancelled', 'expired')
                                 GROUP BY month, specialization
                                 ORDER BY month, cancel_rate_pct DESC
                                 """,

    # 3. Shahar va qabul joyi kesimida hajm — B2B suhbatlari uchun
    "volume_by_city_place": """
                            SELECT city,
                                   place,
                                   sum(bookings) AS bookings,
                                   sum(revenue)  AS revenue
                            FROM daily_booking_agg FINAL
                            WHERE created_date >= today() - 90
                              AND status = 'confirmed'
                            GROUP BY city, place
                            ORDER BY bookings DESC
                            """,
}
