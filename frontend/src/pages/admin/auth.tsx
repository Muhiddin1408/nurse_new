import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, bindAdminLost, getAdminToken, setAdminToken } from '@/lib/api'
import { Spinner } from '@/components/ui'

/**
 * Platforma admini — alohida identity (admin-service, ADR-0005): login + parol → `mfa_token` → TOTP → admin JWT.
 * Mijoz/shifokor sessiyasiga bog'liq emas; refresh yo'q — token muddati tugasa qayta kiriladi.
 */

/** admin-service `AdminDetail` (`GET /v1/admin/auth/me`) */
export interface AdminMe {
  id: string
  login: string
  full_name: string
  is_active: boolean
  last_login_at: string | null
  created_at: string
  role_ids: string[]
  permissions: string[]
}

interface MfaChallenge {
  mfa_token: string
  expires_in: number
  next: string
}

interface AdminSession {
  access: string
  token_type: string
  expires_in: number
}

interface AdminCtx {
  admin: AdminMe | undefined
  isAuthed: boolean
  loading: boolean
  /** 1-qadam: login + parol → MFA tokeni */
  login: (login: string, password: string) => Promise<MfaChallenge>
  /** 2-qadam: TOTP kodi → admin JWT */
  verifyTotp: (mfaToken: string, code: string) => Promise<void>
  logout: () => void
  can: (permission: string) => boolean
}

const Ctx = createContext<AdminCtx | null>(null)

export function AdminAuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const [token, setToken] = useState(getAdminToken)

  const drop = useCallback(() => {
    setAdminToken(null)
    setToken(null)
    qc.removeQueries({ queryKey: ['admin'] })
  }, [qc])

  useEffect(() => bindAdminLost(drop), [drop])

  const me = useQuery({
    queryKey: ['admin', 'me', token],
    queryFn: () => api<AdminMe>('/admin/auth/me', { realm: 'admin' }),
    enabled: !!token,
    staleTime: 5 * 60_000,
    retry: false,
  })

  const login = useCallback(
    (login: string, password: string) => api<MfaChallenge>('/admin/auth/login', { method: 'POST', body: { login, password }, auth: false }),
    [],
  )

  const verifyTotp = useCallback(async (mfaToken: string, code: string) => {
    const s = await api<AdminSession>('/admin/auth/totp', { method: 'POST', body: { mfa_token: mfaToken, code }, auth: false })
    setAdminToken(s.access)
    setToken(s.access)
  }, [])

  const value = useMemo<AdminCtx>(() => {
    const perms = new Set(me.data?.permissions ?? [])
    return {
      admin: me.data,
      isAuthed: !!token && !!me.data,
      loading: !!token && me.isLoading,
      login,
      verifyTotp,
      logout: drop,
      can: (p) => perms.has(p),
    }
  }, [me.data, me.isLoading, token, login, verifyTotp, drop])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAdmin() {
  const c = useContext(Ctx)
  if (!c) throw new Error('useAdmin must be used inside AdminAuthProvider')
  return c
}

/** Admin token'i yo'q yoki yaroqsiz → `/admin/login`. */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const { isAuthed, loading } = useAdmin()
  const loc = useLocation()
  if (loading)
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner className="h-8 w-8" />
      </div>
    )
  if (!isAuthed) return <Navigate to={`/admin/login?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />
  return <>{children}</>
}
