import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError, type RequestOpts } from '@/lib/api'
import type { ClinicDoctorRow, ClinicEarnings, ClinicProfile, ClinicReport, ReportStats, Room } from './types'

const KEY = 'turon.clinic_id'

function readId(): string {
  try {
    return localStorage.getItem(KEY) || ''
  } catch {
    return ''
  }
}

interface ClinicCtx {
  /** Faol klinika (saqlangan tanlov yoki yagona klinika) — bo'sh bo'lsa tanlash kerak */
  clinicId: string
  setClinicId: (id: string) => void
  /** Admin boshqaradigan klinikalar (catalog `GET /v1/clinic/mine`) */
  mine: ClinicProfile[] | undefined
  mineLoading: boolean
  /** `/clinic/<yo'l>` → `/clinic/{clinic_id}/<yo'l>` (catalog / schedule / booking / billing) */
  capi: <T = unknown>(path: string, opts?: RequestOpts) => Promise<T>
}

const Ctx = createContext<ClinicCtx | null>(null)

/**
 * Bitta admin bir nechta klinikani boshqarishi mumkin. Yagona bo'lsa — avtomatik; bir nechta bo'lsa tanlov
 * shu yerda saqlanadi. Har so'rov yo'lida `clinic_id` (resurs authz — catalog `/internal/v1/access`).
 */
export function ClinicProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const [saved, setState] = useState(readId)
  const mineQ = useQuery({ queryKey: ['clinic', 'mine'], queryFn: () => api<ClinicProfile[]>('/clinic/mine'), staleTime: 5 * 60_000 })
  const mine = mineQ.data

  const clinicId = useMemo(() => {
    if (!mine) return ''
    if (saved && mine.some((c) => c.id === saved)) return saved
    return mine.length === 1 ? mine[0].id : ''
  }, [mine, saved])

  const setClinicId = useCallback(
    (id: string) => {
      try {
        if (id) localStorage.setItem(KEY, id)
        else localStorage.removeItem(KEY)
      } catch {
        /* ignore */
      }
      setState(id)
      qc.removeQueries({ queryKey: ['clinic'], predicate: (q) => q.queryKey[1] !== 'mine' })
    },
    [qc],
  )

  const capi = useCallback(
    <T,>(path: string, opts: RequestOpts = {}) => {
      if (!clinicId) return Promise.reject(new ApiError(409, { detail: { code: 'CLINIC_NOT_SELECTED', message: 'Klinikani tanlang' } }))
      return api<T>(path.replace(/^\/clinic(\/|$)/, `/clinic/${clinicId}$1`), opts)
    },
    [clinicId],
  )

  const value = useMemo(
    () => ({ clinicId, setClinicId, mine, mineLoading: mineQ.isLoading, capi }),
    [clinicId, setClinicId, mine, mineQ.isLoading, capi],
  )
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useClinic() {
  const c = useContext(Ctx)
  if (!c) throw new Error('useClinic must be used inside ClinicProvider')
  return c
}

export function useClinicProfile() {
  const { capi, clinicId } = useClinic()
  return useQuery({
    queryKey: ['clinic', clinicId, 'profile'],
    queryFn: () => capi<ClinicProfile>('/clinic/profile'),
    enabled: !!clinicId,
    retry: false,
  })
}

/** Klinika shifokorlari va xonalari ID → nom (schedule / booking faqat ID qaytaradi). */
export function useClinicLookup() {
  const { capi, clinicId } = useClinic()
  const doctors = useQuery({
    queryKey: ['clinic', clinicId, 'doctors'],
    queryFn: () => capi<ClinicDoctorRow[]>('/clinic/doctors'),
    enabled: !!clinicId,
    staleTime: 60_000,
  })
  const rooms = useQuery({
    queryKey: ['clinic', clinicId, 'rooms'],
    queryFn: () => capi<Room[]>('/clinic/rooms'),
    enabled: !!clinicId,
    staleTime: 60_000,
  })
  return {
    doctors: doctors.data ?? [],
    doctorName: (id?: string | null) => (id ? doctors.data?.find((d) => d.doctor_id === id)?.doctor_name : undefined) ?? '',
    roomName: (id?: string | null) => (id ? rooms.data?.find((r) => r.id === id)?.name : undefined) ?? null,
    isLoading: doctors.isLoading || rooms.isLoading,
  }
}

/**
 * Klinika hisoboti — billing-service `GET /v1/clinic/{id}/earnings` (ONLAYN to'langan bronlar) dan shifokorlar
 * kesimida yig'iladi. Klinika kassasida to'langan (at_clinic) qabullar va slot bandligi bu manbada yo'q.
 */
export function useClinicReport(from: string, to: string) {
  const { capi, clinicId } = useClinic()
  const lookup = useClinicLookup()
  const q = useQuery({
    queryKey: ['clinic', clinicId, 'reports', from, to],
    queryFn: () => capi<ClinicEarnings>('/clinic/earnings', { query: { from, to } }),
    enabled: !!clinicId,
  })
  const report = useMemo<ClinicReport | undefined>(() => {
    if (!q.data) return undefined
    const empty = (): ReportStats => ({ revenue_tiyin: 0, net_tiyin: 0, refunds_tiyin: 0, bookings: 0, completed: 0, no_show: 0, cancelled: 0 })
    const add = (s: ReportStats, t: ClinicEarnings['transactions'][number]) => {
      s.revenue_tiyin += t.gross_tiyin
      s.net_tiyin += t.net_tiyin
      s.refunds_tiyin += t.refunds_tiyin
      s.bookings += 1
      if (t.status === 'completed') s.completed += 1
      else if (t.status === 'no_show') s.no_show += 1
      else if (t.status === 'cancelled') s.cancelled += 1
    }
    const total = empty()
    const byDoctor = new Map<string, ReportStats>()
    for (const t of q.data.transactions) {
      add(total, t)
      const key = t.doctor_id ?? ''
      if (!byDoctor.has(key)) byDoctor.set(key, empty())
      add(byDoctor.get(key)!, t)
    }
    return {
      date_from: q.data.period_from,
      date_to: q.data.period_to,
      total,
      doctors: [...byDoctor.entries()].map(([doctor_id, s]) => ({ ...s, doctor_id, doctor: lookup.doctorName(doctor_id) || 'Shifokor' })),
    }
  }, [q.data, lookup])
  return { ...q, data: report }
}
