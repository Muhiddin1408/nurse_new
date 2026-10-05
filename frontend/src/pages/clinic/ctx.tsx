import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type RequestOpts } from '@/lib/api'
import type { ClinicProfile } from './types'

const KEY = 'turon.clinic_id'

function readId(): string {
  try {
    return localStorage.getItem(KEY) || ''
  } catch {
    return ''
  }
}

interface ClinicCtx {
  clinicId: string
  setClinicId: (id: string) => void
  /** clinic_id ni avtomatik qo'shadigan api() */
  capi: <T = unknown>(path: string, opts?: RequestOpts) => Promise<T>
}

const Ctx = createContext<ClinicCtx | null>(null)

/**
 * Bitta admin bir nechta klinikani boshqarishi mumkin. Bitta bo'lsa backend
 * clinic_id'ni o'zi topadi; bir nechta bo'lsa 409 qaytaradi — shunda tanlangan
 * id shu yerda saqlanadi va har so'rovga qo'shiladi.
 */
export function ClinicProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const [clinicId, setState] = useState(readId)

  const setClinicId = useCallback(
    (id: string) => {
      try {
        if (id) localStorage.setItem(KEY, id)
        else localStorage.removeItem(KEY)
      } catch {
        /* ignore */
      }
      setState(id)
      qc.removeQueries({ queryKey: ['clinic'] })
    },
    [qc],
  )

  const capi = useCallback(
    <T,>(path: string, opts: RequestOpts = {}) => api<T>(path, { ...opts, query: { clinic_id: clinicId || undefined, ...opts.query } }),
    [clinicId],
  )

  const value = useMemo(() => ({ clinicId, setClinicId, capi }), [clinicId, setClinicId, capi])
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
    retry: false,
  })
}
