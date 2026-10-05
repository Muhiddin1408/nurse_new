export type Role = 'client' | 'doctor' | 'clinic_admin' | 'platform_admin'

export interface AuthUser {
  id: string
  phone: string
  full_name: string
  role: Role
  available_roles: Role[]
  active_role: Role
}

export interface Tokens { access: string; refresh: string }

export interface Specialization {
  id: string; name: string; slug: string; is_pediatric: boolean; accepts_children: boolean
  min_patient_age: number | null; max_patient_age: number | null; allowed_gender: string
}

export interface Clinic {
  id: string; name: string; city: string; street: string; latitude: string; longitude: string
  rating: string; reviews_count: number
}

export interface DoctorList {
  id: string; full_name: string; experience_years: number; rating: number | null
  reviews_count: number; is_new: boolean; specializations: string[]
  accepts_home_visits: boolean; distance_km?: number | null
}

export interface DoctorDetail extends DoctorList {
  bio: string; license_number: string; home_visit_radius_km: number; clinics: Clinic[]
}

export interface Service { id: string; name: string; place: 'clinic' | 'home' | string; price: string; duration_minutes: number }

export interface Slot { id: string; start_at: string; end_at: string }

export interface Patient {
  id: string; full_name: string; relation: string; birth_date: string; gender: 'male' | 'female'; weight_kg: number | null
}

export interface Address {
  id: string; label: string; city: string; street: string; entrance: string; floor: string
  apartment: string; comment: string; latitude: string; longitude: string; is_default: boolean
}

export type BookingStatus = 'pending_payment' | 'confirmed' | 'in_progress' | 'completed' | 'cancelled' | 'expired' | 'no_show' | string

export interface BookingItem { service_name: string; price: string; duration_minutes: number }

export interface Booking {
  id: string; number: string; status: BookingStatus; status_display: string
  total_price: string; original_price: string | null; discount_amount: string; discount_kind: string
  promo_code: string; created_at: string; items: BookingItem[]
  // backend'ga qo'shilgan o'qish maydonlari (api/booking/serializers.py)
  doctor_id?: string; doctor_name?: string; patient_name?: string
  start_at?: string; end_at?: string; place?: 'clinic' | 'home' | string
  clinic_name?: string; clinic_address?: string; address_text?: string
  payment_mode?: string; prepay_amount?: string
}

export interface CancellationPreview { allowed: boolean; refund_amount: string; penalty_amount: string; reason_code: string }

export interface Dispute {
  id: string; booking_id: string; reason: string; description: string; status: string
  due_at: string; resolution_note: string; refund_id: string | null; created_at: string
}

export interface PackageOffer {
  id: string; doctor_id: string; service_id: string; service_name: string; name: string
  sessions: number; discount_percent: number; validity_days: number; unit_price: string; total_saving: string
}

export interface Enrollment {
  id: string; doctor_id: string; service_id: string; service_name: string; package_name: string
  discount_percent: number; sessions_total: number; sessions_left: number; expires_at: string
}

export interface WaitlistEntry {
  id: string; doctor_id: string; doctor_name: string; date_from: string; date_to: string
  place: string; status: string; created_at: string
}

export interface Preferences {
  language: 'uz' | 'ru'; push_enabled: boolean; telegram_enabled: boolean; sms_enabled: boolean
  marketing_opt_in: boolean; telegram_linked: boolean
}

export interface CheckoutResponse { payment_id: string; checkout_url: string; amount: number; expires_at: string | null }
