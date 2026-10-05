export interface ModDocument {
  id: string
  kind: 'diploma' | 'license' | 'certificate' | 'passport' | string
  original_name: string
  content_type: string
  size: number
  created_at: string
  download_url: string
}

export interface ModerationDoctor {
  id: string
  status: 'draft' | 'pending' | 'approved' | 'rejected' | 'suspended' | string
  full_name: string
  phone: string
  specialization_ids: string[]
  bio: string
  experience_years: number
  education: string
  languages: string[]
  license_number: string
  license_expires_at: string | null
  accepts_home_visits: boolean
  home_visit_radius_km: number
  status_reason: string
  submitted_at: string | null
  moderated_at: string | null
  sla_due_at: string | null
  documents: ModDocument[]
}

export interface ModReview {
  id: string
  rating: number
  comment: string
  author: string | null
  is_anonymous: boolean
  created_at: string
  doctor_reply: string | null
  doctor_replied_at: string | null
  status: string
  moderation_flags: string[]
  booking_number: string
  doctor_id: string
}

export interface Payout {
  id: string
  doctor_id: string
  period_start: string
  period_end: string
  status: 'pending' | 'paid' | string
  bookings_count: number
  gross_amount: string
  platform_fee: string
  provider_fee: string
  refunds_amount: string
  net_amount: string
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
  resolved_client: { label: 'Mijoz foydasiga', tone: 'blue' as const },
  resolved_doctor: { label: 'Shifokor foydasiga', tone: 'green' as const },
}

export const REVIEW_FLAG: Record<string, string> = {
  profanity: "So'kinish",
  phone: 'Telefon raqam',
  link: 'Havola',
}
