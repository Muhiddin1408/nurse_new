export interface ClinicProfile {
  id: string
  name: string
  description: string
  phone: string
  city: string
  street: string
  latitude: string
  longitude: string
  status: 'draft' | 'pending' | 'active' | 'suspended' | string
  rating: string
  reviews_count: number
  working_hours: Record<string, [string, string] | null>
  photo_url: string
}

export interface ClinicAppointment {
  booking_id: string
  number: string
  status: string
  start_at: string
  end_at: string
  doctor: string
  room: string | null
  patient: string
  client_phone: string | null
  payment_mode: string
  total_price: string
  to_collect: string
}

export interface ClinicDoctorRow {
  affiliation_id: string
  doctor_id: string
  doctor: string
  specializations: string[]
  position: string
  status: 'invited' | 'active' | 'paused' | 'ended' | string
  doctor_status: string
  rating: string
  reviews_count: number
}

export interface ScheduleSlot {
  slot_id: string
  start_at: string
  end_at: string
  status: 'free' | 'held' | 'booked' | 'blocked' | string
  block_reason: string
  room: string | null
}

export interface ScheduleColumn {
  doctor_id: string
  doctor: string
  slots: ScheduleSlot[]
}

export interface Room {
  id: string
  name: string
  floor: string
  equipment: string[]
  is_active: boolean
}

export interface ClinicRule {
  rule_id: string
  doctor: string
  weekday: number
  start_time: string
  end_time: string
  room_id: string | null
  room: string | null
}

export interface ServiceRead {
  id: string
  specialization_id: string
  name: string
  name_ru: string
  description: string
  place: string
  duration_minutes: number
  price: string
  is_active: boolean
}

export interface Closure {
  id: string
  date: string
  reason: string
}

export interface ReportStats {
  revenue: string
  slots_total: number
  slots_booked: number
  occupancy_pct: number
  completed_or_no_show: number
  no_show: number
  no_show_pct: number
  cancelled: number
}

export interface ClinicReport {
  date_from: string
  date_to: string
  total: ReportStats
  doctors: (ReportStats & { doctor_id: string; doctor: string })[]
}
