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

export interface DoctorProfile {
  id: string
  status: string
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
}

export interface Appointment {
  id: string
  number: string
  status: string
  start_at: string
  end_at: string
  place: 'home' | 'clinic' | string
  clinic: { id: string; name: string } | null
  patient: { full_name: string; age: number | null; gender: string; weight_kg: number | null }
  services: { name: string; duration_minutes: number }[]
  client_comment: string
  client_phone: string | null
  contact_available_from: string | null
  started_at: string | null
  completed_at: string | null
  note: string
  address?: { city: string; street: string; latitude: string; longitude: string; entrance?: string; floor?: string; apartment?: string; comment?: string }
  patient_history?: { date: string; status: string; services: string[]; note: string }[]
}

export interface ScheduleSlot {
  slot_id: string
  start_at: string
  end_at: string
  status: 'free' | 'held' | 'booked' | 'blocked' | string
  block_reason: string
  block_note: string
  place: string
  clinic: string | null
  booking: null | { id: string; number: string; status: string; patient: string; services: string[] }
}

export interface Conflict {
  booking_id: string
  booking_number: string
  status: string
  start_at: string
  end_at: string
  patient: string
}

export interface ServiceRead {
  id: string
  specialization_id: string
  name: string
  name_ru: string
  description: string
  place: 'clinic' | 'home' | string
  duration_minutes: number
  price: string
  is_active: boolean
}

export interface Summary {
  period_from: string
  period_to: string
  bookings_count: number
  gross: string
  platform_fee: string
  provider_fee: string
  refunds: string
  net: string
  paid_out: string
  awaiting_payout: string
}

export interface Transaction {
  booking_id: string
  number: string
  date: string
  patient: string
  services: string[]
  status: string
  gross: string
  platform_fee: string
  provider_fee: string
  refunds: string
  net: string
  payout_id: string | null
  payout_status: string | null
}

export interface Payout {
  id: string
  period_start: string
  period_end: string
  status: string
  bookings_count: number
  gross_amount: string
  platform_fee: string
  provider_fee: string
  refunds_amount: string
  net_amount: string
  paid_at: string | null
  bank_reference: string
}

export interface Review {
  id: string
  rating: number
  comment: string
  author: string | null
  is_anonymous: boolean
  created_at: string
  doctor_reply: string | null
  doctor_replied_at: string | null
}

// ---- constants -------------------------------------------------------------
export const APPT_STATUS = {
  confirmed: { label: 'Tasdiqlangan', tone: 'blue' as const },
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

export function useSpecs() {
  return useQuery({ queryKey: ['specializations'], queryFn: () => api<Specialization[]>('/catalog/specializations', { auth: false }), staleTime: 600_000 })
}

export function hhmm(t: string) {
  return t.slice(0, 5)
}

// ---- 409 ScheduleConflict ----------------------------------------------------
export function getConflict(e: unknown): { detail: string; conflicts: Conflict[]; options: string[] } | null {
  if (e instanceof ApiError && e.status === 409 && e.data && typeof e.data === 'object' && 'conflicts' in e.data) {
    const d = e.data as { detail: string; conflicts: Conflict[]; options: string[] }
    return { detail: d.detail, conflicts: d.conflicts || [], options: d.options || [] }
  }
  return null
}

const RES_LABEL: Record<string, { title: string; text: string }> = {
  keep_bookings: { title: 'Qabullarni saqlash', text: "Tasdiqlangan qabullar o'z joyida qoladi, faqat yangi slotlar o'zgaradi." },
  cancel_and_refund: { title: 'Bekor qilib pulini qaytarish', text: "Ta'sirlangan qabullar bekor qilinadi, mijozlarga 100% qaytariladi va xabar yuboriladi." },
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
          <div key={c.booking_id} className="flex items-center justify-between rounded-2xl bg-ink-50 px-4 py-2.5 text-sm">
            <span className="font-semibold text-ink-800">{c.patient}</span>
            <span className="text-ink-500">
              {relativeDateTime(c.start_at)} · #{c.booking_number}
            </span>
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
