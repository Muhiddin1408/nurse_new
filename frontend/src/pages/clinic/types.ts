// Klinika admini paneli tiplari — mikroservislar javoblari (`/v1/clinic/{clinic_id}/...`). Pul — tiyin.

/** catalog-service `ClinicAdminOut` */
export interface ClinicProfile {
  id: string
  name: string
  description: string
  phone: string
  city: string
  street: string
  latitude: string
  longitude: string
  status: 'draft' | 'active' | 'suspended' | string
  status_reason: string
  rating: string | null
  reviews_count: number
  is_new: boolean
  working_hours: Record<string, [string, string] | null>
  photo_url: string | null
  created_at: string
}

/** booking-service `AppointmentOut` (klinika ko'rinishi). Shifokor/xona nomi — `useClinicLookup`. */
export interface ClinicAppointment {
  id: string
  number: string
  status: string
  place: string
  start_at: string
  end_at: string
  doctor_id: string
  clinic_id: string | null
  patient: { full_name?: string; birth_date?: string; gender?: string }
  services: { service_id: string; service_name: string; price_tiyin: number; duration_minutes: number }[]
  client_comment: string
  started_at: string | null
  completed_at: string | null
  note: string
  total_tiyin: number
  /** Klinikada olinadigan qoldiq */
  to_collect_tiyin: number
}

/** catalog-service `ClinicDoctorOut` */
export interface ClinicDoctorRow {
  affiliation_id: string
  clinic_id: string
  doctor_id: string
  doctor_name: string
  position: string
  status: 'invited' | 'active' | 'paused' | 'ended' | 'declined' | string
  doctor_status: string
  updated_at: string
}

/** schedule-service `CalendarItem` */
export interface ScheduleSlot {
  slot_id: string
  start_at: string
  end_at: string
  status: 'free' | 'held' | 'booked' | 'blocked' | string
  block_reason: string
  block_note: string
  place: string
  clinic_id: string | null
  room_id: string | null
  booking_id: string | null
}

/** Kun ustuni: shifokor bo'yicha (schedule `{date, doctors: {doctor_id: [slot]}}` dan) */
export interface ScheduleColumn {
  doctor_id: string
  doctor: string
  slots: ScheduleSlot[]
}

/** catalog-service `RoomOut` */
export interface Room {
  id: string
  clinic_id: string
  name: string
  floor: string
  equipment: string[]
  is_active: boolean
}

/** schedule-service `{doctor_id, rule}` → bitta qator (nomlar — lookup'dan) */
export interface ClinicRule {
  rule_id: string
  doctor_id: string
  doctor: string
  weekday: number
  start_time: string
  end_time: string
  room_id: string | null
  room: string | null
}

/** catalog-service `ServiceOut` */
export interface ServiceRead {
  id: string
  doctor_id: string | null
  clinic_id: string | null
  specialization_id: string
  name: string
  name_ru: string
  description: string
  place: string
  duration_minutes: number
  price_tiyin: number
  is_active: boolean
}

export interface Closure {
  id: string
  date: string
  reason: string
}

/** billing-service `EarningsOut` (klinika bo'yicha) */
export interface ClinicEarnings {
  period_from: string
  period_to: string
  bookings_count: number
  gross_tiyin: number
  platform_fee_tiyin: number
  provider_fee_tiyin: number
  refunds_tiyin: number
  net_tiyin: number
  paid_out_tiyin: number
  awaiting_payout_tiyin: number
  transactions: {
    booking_id: string
    doctor_id: string | null
    number: string
    date: string | null
    services: string[]
    status: string
    gross_tiyin: number
    refunds_tiyin: number
    net_tiyin: number
  }[]
}

/** Hisobot ko'rsatkichlari — onlayn to'langan bronlar bo'yicha (billing) */
export interface ReportStats {
  revenue_tiyin: number
  net_tiyin: number
  refunds_tiyin: number
  bookings: number
  completed: number
  no_show: number
  cancelled: number
}

export interface ClinicReport {
  date_from: string
  date_to: string
  total: ReportStats
  doctors: (ReportStats & { doctor_id: string; doctor: string })[]
}
