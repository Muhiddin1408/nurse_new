import { useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { relativeDateTime } from '@/lib/format'
import type { DoctorDetail, Specialization } from '@/lib/types'
import { Button, Modal, cx } from '@/components/ui'

// ---- types -----------------------------------------------------------------
export interface OnboardingStatus {
  status: 'draft' | 'pending' | 'approved' | 'rejected' | 'suspended' | string
  reason: string
  missing: string[]
  submitted_at: string | null
  sla_due_at: string | null
}

/** catalog-service `DoctorPrivate` (onboarding va kabinet profili) */
export interface DoctorProfile {
  id: string
  user_id: string
  status: string
  status_reason: string
  full_name: string
  specialization_ids: string[]
  bio: string
  experience_years: number
  education: string
  languages: string[]
  license_number: string
  license_expires_at: string | null
  accepts_home_visits: boolean
  home_visit_radius_km: number
  home_base_latitude: string | null
  home_base_longitude: string | null
  clinic_payment_mode: string
  follow_up_discount_percent: number
  photo_url: string | null
  submitted_at: string | null
}

/** Bron paytidagi bemor nusxasi (booking-service) */
export interface PatientSnapshot {
  full_name?: string
  relation?: string
  birth_date?: string
  gender?: string
  weight_kg?: number | null
}

/** booking-service `AppointmentOut` (+ `patient_history` tafsilotda). Klinika nomi — catalog'dan (`clinic_id`). */
export interface Appointment {
  id: string
  number: string
  status: string
  start_at: string
  end_at: string
  place: 'home' | 'clinic' | string
  clinic_id: string | null
  patient: PatientSnapshot
  services: { service_id: string; service_name: string; price_tiyin: number; duration_minutes: number }[]
  client_comment: string
  started_at: string | null
  completed_at: string | null
  note: string
  total_tiyin: number
  /** Klinikada yoki uyda olinadigan qoldiq (zaklad / at_clinic) */
  to_collect_tiyin: number
  address?: { city?: string; district?: string; street?: string; latitude?: string; longitude?: string; entrance?: string; floor?: string; apartment?: string; comment?: string } | null
  patient_history?: { date: string; status: string; services: string[]; note: string }[]
}

/** schedule-service kalendari (`CalendarItem`) + booking'dan bron qisqachasi (`booking_id` bo'yicha) */
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
  booking: null | { id: string; number: string; status: string; patient: string; services: string[] }
}

/** schedule-service `ConflictSlot` (409 LIVE_BOOKINGS) */
export interface Conflict {
  slot_id: string
  booking_id: string | null
  status: string
  start_at: string
  end_at: string
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
  place: 'clinic' | 'home' | string
  duration_minutes: number
  price_tiyin: number
  is_active: boolean
}

/** billing-service `EarningsOut` */
export interface Summary {
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
}

export interface Transaction {
  booking_id: string
  number: string
  date: string | null
  services: string[]
  status: string
  gross_tiyin: number
  platform_fee_tiyin: number
  provider_fee_tiyin: number
  refunds_tiyin: number
  net_tiyin: number
  payout_id: string | null
  payout_status: string | null
}

export interface Payout {
  id: string
  doctor_id: string
  period_start: string
  period_end: string
  status: string
  bookings_count: number
  gross_tiyin: number
  platform_fee_tiyin: number
  provider_fee_tiyin: number
  refunds_tiyin: number
  net_tiyin: number
  paid_at: string | null
  bank_reference: string
}

/** review-service `PublicReviewOut` */
export interface Review {
  id: string
  doctor_id: string
  rating: number
  comment: string
  author: string | null
  created_at: string
  doctor_reply: string | null
  doctor_replied_at: string | null
}

// ---- constants -------------------------------------------------------------
export const APPT_STATUS = {
  confirmed: { label: 'Tasdiqlangan', tone: 'blue' as const },
  in_progress: { label: 'Qabulda', tone: 'violet' as const },
  completed: { label: 'Yakunlangan', tone: 'green' as const },
  cancelled: { label: 'Bekor qilingan', tone: 'gray' as const },
  no_show: { label: 'Kelmadi', tone: 'red' as const },
  pending_payment: { label: "To'lov kutilmoqda", tone: 'amber' as const },
  expired: { label: "Muddati o'tgan", tone: 'gray' as const },
}

export const LANGUAGES: { value: string; label: string }[] = [
  { value: 'uz', label: "O'zbek" },
  { value: 'ru', label: 'Rus' },
  { value: 'en', label: 'Ingliz' },
  { value: 'kk', label: 'Qozoq' },
  { value: 'tg', label: 'Tojik' },
  { value: 'tr', label: 'Turk' },
]

export const SLOT_MINUTES = [10, 15, 20, 30, 40, 45, 60, 90, 120]

export const MISSING_LABEL: Record<string, string> = {
  full_name: "To'liq ism",
  specializations: 'Mutaxassislik',
  specialization_ids: 'Mutaxassislik',
  experience_years: 'Tajriba',
  license_number: 'Litsenziya raqami',
  license_expires_at: 'Litsenziya muddati',
  diploma: 'Diplom hujjati',
  license: 'Litsenziya hujjati',
  documents: 'Hujjatlar',
  bio: "O'zingiz haqingizda",
  education: "Ma'lumot",
}

// ---- hooks -----------------------------------------------------------------
export function useDoctorProfile() {
  return useQuery({ queryKey: ['doctor', 'profile'], queryFn: () => api<DoctorProfile>('/doctor/onboarding/profile') })
}

/** Shifokorning faol klinikalari — ochiq katalog kartochkasidan olinadi. */
export function useMyClinics() {
  const profile = useDoctorProfile()
  const id = profile.data?.id
  return useQuery({
    queryKey: ['doctor', 'public', id],
    queryFn: () => api<DoctorDetail>(`/catalog/doctors/${id}`, { auth: false }),
    enabled: !!id,
  })
}

/** clinic_id → nom (qabullar faqat ID saqlaydi; shifokorning klinikalari — ochiq kartochkadan) */
export function useClinicName() {
  const clinics = useMyClinics()
  return (id: string | null | undefined) => (id ? clinics.data?.clinics.find((c) => c.id === id)?.name : undefined)
}

export function useSpecs() {
  return useQuery({ queryKey: ['specializations'], queryFn: () => api<Specialization[]>('/catalog/specializations', { auth: false }), staleTime: 600_000 })
}

export function hhmm(t: string) {
  return t.slice(0, 5)
}

// ---- 409 ScheduleConflict ----------------------------------------------------
/** schedule-service: `409 {"detail": {"code": "LIVE_BOOKINGS", "message", "conflicts", "options"}}` */
export function getConflict(e: unknown): { detail: string; conflicts: Conflict[]; options: string[] } | null {
  if (e instanceof ApiError && e.status === 409 && e.code === 'LIVE_BOOKINGS') {
    const d = (e.data as { detail?: { conflicts?: Conflict[]; options?: string[] } })?.detail ?? {}
    return { detail: e.detail, conflicts: d.conflicts ?? [], options: d.options ?? ['keep_bookings'] }
  }
  return null
}

// Jadval qabulni o'zi bekor qilmaydi (egasi — booking): kerak bo'lsa qabulni "Qabullar" bo'limidan bekor qiling
const RES_LABEL: Record<string, { title: string; text: string }> = {
  keep_bookings: { title: 'Qabullarni saqlash', text: "Tasdiqlangan qabullar o'z joyida qoladi, faqat yangi slotlar o'zgaradi." },
}

export function ConflictModal({
  conflict,
  onClose,
  onResolve,
  loading,
}: {
  conflict: { detail: string; conflicts: Conflict[]; options: string[] } | null
  onClose: () => void
  onResolve: (resolution: string) => void
  loading?: boolean
}) {
  const [choice, setChoice] = useState('keep_bookings')
  const opts = (conflict?.options ?? []).filter((o) => o in RES_LABEL)
  return (
    <Modal
      open={!!conflict}
      onClose={onClose}
      title="Qabullar bilan to'qnashuv"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Boshqa sana tanlash
          </Button>
          <Button variant={choice === 'cancel_and_refund' ? 'danger' : 'primary'} loading={loading} onClick={() => onResolve(choice)}>
            Davom etish
          </Button>
        </>
      }
    >
      <div className="mb-4 flex gap-3 rounded-2xl bg-amber-50 p-4 text-sm text-amber-800">
        <AlertTriangle className="h-5 w-5 shrink-0" />
        <span>{conflict?.detail}</span>
      </div>
      <div className="mb-4 max-h-48 space-y-2 overflow-y-auto">
        {conflict?.conflicts.map((c) => (
          <div key={c.slot_id} className="flex items-center justify-between rounded-2xl bg-ink-50 px-4 py-2.5 text-sm">
            <span className="font-semibold text-ink-800">{relativeDateTime(c.start_at)}</span>
            <span className="text-ink-500">{c.status === 'booked' ? 'Tasdiqlangan qabul' : "To'lov kutilmoqda"}</span>
          </div>
        ))}
      </div>
      <div className="space-y-2">
        {opts.map((o) => (
          <button
            key={o}
            type="button"
            onClick={() => setChoice(o)}
            className={cx('w-full rounded-2xl border-2 p-4 text-left transition', choice === o ? 'border-brand-500 bg-brand-50/50' : 'border-ink-100 hover:border-ink-200')}
          >
            <div className="font-bold text-ink-900">{RES_LABEL[o].title}</div>
            <div className="mt-0.5 text-sm text-ink-500">{RES_LABEL[o].text}</div>
          </button>
        ))}
      </div>
    </Modal>
  )
}

export function SectionCard({ title, subtitle, action, children, className }: { title?: ReactNode; subtitle?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx('card p-5 sm:p-6', className)}>
      {(title || action) && (
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            {title && <h2 className="text-lg font-bold text-ink-900">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-sm text-ink-500">{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  )
}
