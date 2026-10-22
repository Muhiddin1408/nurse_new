// API tiplari — mikroservislar sxemasi (`/v1/*`, servislarning /openapi.json). Pul — tiyin (butun son, ADR-0007).

/** Foydalanuvchi rollari (auth-service). Admin — alohida identity (`/admin/login`), bu yerda yo'q. */
export type Role = 'client' | 'doctor' | 'clinic_admin'

/** Sessiyadagi foydalanuvchi: auth `user` (rollar) + user-service `/users/me` (ism). */
export interface AuthUser {
  id: string
  phone: string
  full_name: string
  role: Role
  available_roles: Role[]
  active_role: Role
  doctor_id: string | null
}

export interface Tokens { access: string; refresh: string }

export interface Specialization {
  id: string; name: string; name_ru: string; slug: string; is_pediatric: boolean; accepts_children: boolean
  min_patient_age: number | null; max_patient_age: number | null; allowed_gender: string
}

export interface SpecializationBrief { id: string; name: string; name_ru: string }

export interface Clinic {
  id: string; name: string; description: string; phone: string; city: string; street: string
  latitude: string; longitude: string; working_hours: Record<string, unknown>; photo_url: string | null
  rating: string | null; reviews_count: number; is_new: boolean
}

export interface DoctorList {
  id: string; full_name: string; experience_years: number; rating: string | null
  reviews_count: number; is_new: boolean; specializations: SpecializationBrief[]
  accepts_home_visits: boolean; photo_url: string | null; distance_km?: number | null
}

export interface DoctorDetail extends DoctorList {
  bio: string; education: string; languages: string[]; license_number: string
  home_visit_radius_km: number; clinics: Clinic[]
}

/** search-service natijasi (Elasticsearch; ES ishlamasa `degraded`). */
export interface DoctorCard {
  id: string; full_name: string; specialization_ids: string[]; specialization_names: string[]
  experience_years: number; languages: string[]; rating: number | null; reviews_count: number
  accepts_home_visits: boolean; places: string[] | null; min_price_tiyin: number | null
  clinics: { id: string; name: string; city: string }[] | null; next_available_at: string | null
  distance_km: number | null
}

export interface SearchResults { engine: string; degraded: boolean; total: number | null; items: DoctorCard[] }

export interface Service {
  id: string; name: string; name_ru: string; place: 'clinic' | 'home' | string
  duration_minutes: number; price_tiyin: number; clinic_id: string | null
}

export interface Slot { id: string; start_at: string; end_at: string }

export interface Patient {
  id: string; full_name: string; relation: string; birth_date: string; gender: 'male' | 'female'; weight_kg: number | null
}

export interface Address {
  id: string; label: string; city: string; district: string; street: string; entrance: string; floor: string
  apartment: string; comment: string; latitude: string; longitude: string; is_default: boolean
}

export type BookingStatus = 'pending_payment' | 'confirmed' | 'in_progress' | 'completed' | 'cancelled' | 'expired' | 'no_show' | string

export interface BookingItem { service_id: string; service_name: string; price_tiyin: number; duration_minutes: number }

/** booking-service `BookingOut`. Bemor va manzil — bron paytidagi nusxa. Shifokor/klinika nomi — catalog'dan. */
export interface Booking {
  id: string; number: string; status: BookingStatus; place: 'clinic' | 'home' | string
  start_at: string; end_at: string; doctor_id: string; clinic_id: string | null
  patient: { full_name?: string; relation?: string; birth_date?: string; gender?: string } & Record<string, unknown>
  address: Partial<Address> | null
  items: BookingItem[]
  original_tiyin: number; price_adjust_percent: number; discount_kind: string; discount_tiyin: number
  promo_code: string; total_tiyin: number; prepay_tiyin: number; payment_mode: string
  payment_due_at: string | null; paid_tiyin: number; rescheduled_count: number; client_comment: string
  cancelled_by: string; cancel_reason_code: string; created_at: string
}

export interface BookingPage { results: Booking[]; next_cursor: string | null }

export interface CancellationPreview { allowed: boolean; refund_tiyin: number; penalty_tiyin: number; reason_code: string }

export interface Quote {
  original_tiyin: number; price_adjust_percent: number; discount_kind: string; discount_tiyin: number
  promo_code: string; total_tiyin: number; payment_mode: string; prepay_tiyin: number; items: unknown[]
}

export interface RebookOut {
  doctor_id: string; doctor_bookable: boolean; place: string; clinic_id: string | null
  patient_id: string; address_id: string | null; service_ids: string[]; slots: Slot[]
}

export interface Dispute {
  id: string; booking_id: string; booking_number: string; reason: string; description: string; status: string
  due_at: string; resolution: string | null; refund_tiyin: number; refunded_tiyin: number
  resolution_note: string; resolved_at: string | null; closed_at: string | null; created_at: string
}

export interface PackageOffer {
  id: string; doctor_id: string; service_id: string; service_name: string; name: string; sessions: number
  discount_percent: number; validity_days: number; is_active: boolean; unit_price_tiyin: number; total_saving_tiyin: number
}

export interface Enrollment {
  id: string; package_id: string; doctor_id: string; service_id: string; package_name: string
  discount_percent: number; sessions_total: number; sessions_left: number; expires_at: string
}

export interface WaitlistEntry {
  id: string; doctor_id: string; date_from: string; date_to: string
  place: string; status: string; notified_at: string | null; created_at: string
}

/** notification-service. Til — user-service `preferred_language`. */
export interface Preferences {
  push_enabled: boolean; telegram_enabled: boolean; sms_enabled: boolean; marketing_opt_in: boolean; telegram_linked: boolean
}

export interface Me {
  id: string; phone: string | null; roles: string[]; full_name: string; preferred_language: 'uz' | 'ru' | string
  avatar_url: string | null; avatar_thumbnail_url: string | null; created_at: string
}

export interface Payment {
  id: string; booking_id: string; booking_number: string; provider: string; status: string
  amount_tiyin: number; created_at: string; captured_at: string | null
}

export interface CheckoutResponse { payment: Payment; checkout_url: string; expires_at: string | null }

export interface Review {
  id: string; doctor_id: string; rating: number; comment: string; author: string | null; created_at: string
  doctor_reply: string | null; doctor_replied_at: string | null
}
