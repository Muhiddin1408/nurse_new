// Moderatsiya (platforma admini) tiplari — `/v1/admin/*` javoblari, admin JWT bilan (`realm: 'admin'`). Pul — tiyin.

/** catalog-service `DocumentOut` — fayl havolasi alohida: `GET .../documents/{id}/url` (qisqa muddatli) */
export interface ModDocument {
  id: string
  kind: 'diploma' | 'license' | 'certificate' | 'passport' | string
  file_id: string
  content_type: string
  created_at: string
}

/** catalog-service `ModerationDoctor` */
export interface ModerationDoctor {
  id: string
  user_id: string
  status: 'draft' | 'pending' | 'approved' | 'rejected' | 'suspended' | string
  status_reason: string
  full_name: string
  bio: string
  education: string
  experience_years: number
  languages: string[]
  license_number: string
  license_expires_at: string | null
  accepts_home_visits: boolean
  home_visit_radius_km: number
  clinic_payment_mode: string
  follow_up_discount_percent: number
  photo_url: string | null
  specialization_ids: string[]
  submitted_at: string | null
  documents: ModDocument[]
  moderated_at: string | null
  sla_due_at: string | null
}

/** review-service `AdminReviewOut` */
export interface ModReview {
  id: string
  booking_id: string
  booking_number: string
  client_id: string
  doctor_id: string
  clinic_id: string | null
  rating: number
  comment: string
  is_anonymous: boolean
  author_name: string
  status: 'published' | 'pending' | 'rejected' | string
  moderation_flags: string[]
  moderated_at: string | null
  reject_reason: string
  doctor_reply: string
  created_at: string
}

/** dispute-service `AdminDisputeOut` */
export interface AdminDispute {
  id: string
  booking_id: string
  booking_number: string
  reason: string
  description: string
  status: 'open' | 'resolved' | 'closed' | string
  due_at: string
  resolution: 'refund_full' | 'refund_partial' | 'rejected' | null
  refund_tiyin: number
  refunded_tiyin: number
  resolution_note: string
  resolved_at: string | null
  closed_at: string | null
  created_at: string
  evidence: { file_id: string; content_type: string; created_at: string; url: string | null }[]
  client_id: string
  doctor_id: string
  paid_tiyin: number
  doctor_liable: boolean | null
  resolved_by: string | null
  sla_breached_at: string | null
  refund_id: string | null
}

/** billing-service `PayoutOut` */
export interface Payout {
  id: string
  doctor_id: string
  period_start: string
  period_end: string
  status: 'pending' | 'paid' | string
  bookings_count: number
  gross_tiyin: number
  platform_fee_tiyin: number
  provider_fee_tiyin: number
  refunds_tiyin: number
  net_tiyin: number
  paid_at: string | null
  bank_reference: string
}

export const DOCTOR_STATUS = {
  draft: { label: 'Qoralama', tone: 'gray' as const },
  pending: { label: 'Moderatsiyada', tone: 'amber' as const },
  approved: { label: 'Tasdiqlangan', tone: 'green' as const },
  rejected: { label: 'Rad etilgan', tone: 'red' as const },
  suspended: { label: "To'xtatilgan", tone: 'violet' as const },
}

export const DOC_KIND: Record<string, string> = {
  diploma: 'Diplom',
  license: 'Litsenziya',
  certificate: 'Sertifikat',
  passport: 'Pasport',
}

export const LANG: Record<string, string> = { uz: "O'zbek", ru: 'Rus', en: 'Ingliz', kk: 'Qozoq', tg: 'Tojik', tr: 'Turk' }

export const DISPUTE_REASON: Record<string, string> = {
  doctor_no_show: 'Shifokor kelmadi',
  poor_service: 'Sifatsiz xizmat',
  wrong_charge: "Noto'g'ri to'lov",
  other: 'Boshqa',
}

export const DISPUTE_STATUS = {
  open: { label: 'Ochiq', tone: 'amber' as const },
  resolved: { label: 'Hal qilingan', tone: 'blue' as const },
  closed: { label: 'Yopilgan', tone: 'green' as const },
}

export const DISPUTE_RESOLUTION: Record<string, string> = {
  refund_full: "To'liq qaytarish",
  refund_partial: 'Qisman qaytarish',
  rejected: 'Rad etildi',
}

export const REVIEW_FLAG: Record<string, string> = {
  profanity: "So'kinish",
  phone: 'Telefon raqam',
  link: 'Havola',
}
