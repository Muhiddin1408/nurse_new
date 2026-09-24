
import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Rate } from "k6/metrics";

const errorRate = new Rate("errors");
const slotLatency = new Trend("slot_latency_ms");

export const options = {
  // Bosqichma-bosqich yuklama — real trafik ham shunday o'sadi, birdan emas.
  // Bir zumda 10000 VU tashlash noreal va faqat tarmoqni buzadi.
  stages: [
    { duration: "30s", target: 50 },    // isinish
    { duration: "1m", target: 200 },    // normal yuk
    { duration: "1m", target: 500 },    // yuqori yuk
    { duration: "2m", target: 1000 },   // pik
    { duration: "1m", target: 0 },      // soviش
  ],

  // ╔════════════════════════════════════════════════════════════════════╗
  // ║  THRESHOLD — bu SIZNING SLA'ingiz. Test shu chegaralarga qarab      ║
  // ║  o'tdi/yiqildi deb baholanadi. p95 tanlangani muhim: o'rtacha       ║
  // ║  (mean) yolg'onchi ko'rsatkich — u sekin so'rovlarni yashiradi.     ║
  // ║  p95 = "so'rovlarning 95% shu vaqtdan tez" degani.                  ║
  // ╚════════════════════════════════════════════════════════════════════╝
  thresholds: {
    http_req_duration: ["p(95)<100", "p(99)<250"],  // maqsad: p95 100ms dan tez
    errors: ["rate<0.01"],                            // 1% dan kam xato
    http_req_failed: ["rate<0.01"],
  },
};

// Test uchun oldindan yaratilgan shifokor ID'lari.
// Bitta ID'ni urg'ochilamang — real hayotda so'rovlar turli shifokorlarga
// taqsimlanadi, va bu keshlash xatti-harakatiga jiddiy ta'sir qiladi.
//
// ╔══════════════════════════════════════════════════════════════════════╗
// ║  doctor_ids.json REPOZITORIYGA QO'YILMAYDI — u GENERATSIYA           ║
// ║  QILINADIGAN artefakt, xuddi build natijasi kabi. ID'lar har bir      ║
// ║  bazada boshqacha, shuning uchun qotirib yozilgan fayl boshqa         ║
// ║  muhitda 404 beradi va siz keshni emas, "topilmadi" yo'lini test      ║
// ║  qilib qo'yasiz.                                                      ║
// ║                                                                       ║
// ║  Test oldidan generatsiya qiling:                                     ║
// ║      python manage.py export_doctor_ids --out apps/loadtest/doctor_ids.json
// ╚══════════════════════════════════════════════════════════════════════╝
const DOCTOR_IDS = loadDoctorIds();

function loadDoctorIds() {
  // ⬇ k6 `open()` fayl bo'lmasa tushunarsiz ichki xato bilan yiqiladi.
  // Uni ushlab, NIMA QILISH KERAKLIGINI aytadigan xabarga almashtiramiz —
  // yuk testi 5 daqiqa ishlagandan keyin emas, DARHOL.
  let raw;
  try {
    raw = open("./doctor_ids.json");
  } catch (e) {
    throw new Error(
      "doctor_ids.json topilmadi. Avval generatsiya qiling:\n" +
        "  python manage.py export_doctor_ids --out apps/loadtest/doctor_ids.json"
    );
  }

  const ids = JSON.parse(raw);

  if (!Array.isArray(ids) || ids.length === 0) {
    // Bo'sh ro'yxat eng xavfli holat: `ids[Math.floor(Math.random() * 0)]`
    // `undefined` beradi va test JIM-JITLIK bilan `/doctors/undefined/slots`
    // ga 404 ota boshlaydi. Threshold'lar yiqiladi, sabab esa noma'lum
    // qoladi. Shuning uchun shu yerda to'xtatamiz.
    throw new Error(
      "doctor_ids.json bo'sh — bazada APPROVED shifokor yo'q. " +
        "Avval test ma'lumot yarating, keyin export_doctor_ids ni qayta ishga tushiring."
    );
  }

  return ids;
}

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";

export default function () {
  const doctorId = DOCTOR_IDS[Math.floor(Math.random() * DOCTOR_IDS.length)];
  // Parametrsiz chaqiruv = bugungi kun = KESHLANADIGAN yo'l.
  // `?date_from=&date_to=` bersangiz oraliq rejimiga o'tasiz va u
  // keshlanmaydi — ya'ni Faza 6 ning butun maqsadini o'tkazib yuborasiz.
  const url = `${BASE_URL}/api/v1/schedule/doctors/${doctorId}/slots`;

  const res = http.get(url);

  const ok = check(res, {
    "status 200": (r) => r.status === 200,
    "javob bo'sh emas": (r) => r.body && r.body.length > 2,
    "p95 ичида": (r) => r.timings.duration < 250,
  });

  errorRate.add(!ok);
  slotLatency.add(res.timings.duration);

  // Real foydalanuvchi so'rovlar orasida o'ylaydi. sleep siz uzluksiz
  // otsangiz, bu realdan ko'ra og'irroq sun'iy yuk bo'ladi.
  sleep(Math.random() * 2);
}
 