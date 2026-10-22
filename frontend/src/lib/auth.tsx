import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, bindAuthHandlers, getTokens, readStored, setTokens, writeStored } from './api'
import type { AuthUser, Me, Role, Tokens } from './types'

/** auth-service `POST /v1/auth/otp/verify` */
interface LoginOut {
  access: string
  refresh: string
  is_new_user: boolean
  user: { id: string; phone: string; roles: string[]; available_roles: string[]; active_role: string; doctor_id: string | null }
}

/** auth-service `GET /v1/auth/me` — token egasining joriy rollari (shifokor tasdiqlangach yangilanadi) */
interface AuthMe { id: string; phone: string | null; roles: string[]; active_role: string; doctor_id: string | null }

interface VerifyResponse { is_new_user: boolean; user: AuthUser }

interface AuthCtx {
  user: AuthUser | null
  isAuthed: boolean
  requestOtp: (phone: string) => Promise<{ detail?: string; debug_code?: string | null; retry_after?: number }>
  verifyOtp: (phone: string, code: string) => Promise<VerifyResponse>
  switchRole: (role: Role) => Promise<void>
  updateUser: (patch: Partial<AuthUser>) => void
  /** Rollar va ismni serverdan qayta o'qiydi (masalan, shifokor tasdiqlangandan keyin) */
  refreshUser: () => Promise<AuthUser | null>
  logout: (all?: boolean) => Promise<void>
}

const Ctx = createContext<AuthCtx | null>(null)

export const ROLE_LABEL: Record<Role, string> = {
  client: 'Mijoz',
  doctor: 'Shifokor',
  clinic_admin: 'Klinika admini',
}

export const ROLE_HOME: Record<Role, string> = {
  client: '/account',
  doctor: '/doctor',
  clinic_admin: '/clinic',
}

const KNOWN = new Set<string>(['client', 'doctor', 'clinic_admin'])
const asRoles = (xs: string[]) => xs.filter((r): r is Role => KNOWN.has(r))

function toUser(a: { id: string; phone: string | null; roles: string[]; active_role: string; doctor_id: string | null }, fullName: string): AuthUser {
  const roles = asRoles(a.roles)
  const active = (KNOWN.has(a.active_role) ? a.active_role : roles[0] ?? 'client') as Role
  return { id: a.id, phone: a.phone ?? '', full_name: fullName, role: active, active_role: active, available_roles: roles, doctor_id: a.doctor_id }
}

async function fullName(): Promise<string> {
  try {
    return (await api<Me>('/users/me')).full_name
  } catch {
    return '' // user-service vaqtincha ishlamasa — sessiya baribir ochiladi
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const [state, setState] = useState<{ tokens: Tokens | null; user: AuthUser | null }>(() => {
    const s = readStored()
    return { tokens: s.tokens, user: (s.user as AuthUser) ?? null }
  })

  const persist = useCallback((tokens: Tokens | null, user: AuthUser | null) => {
    setTokens(tokens)
    writeStored({ tokens, user })
    setState({ tokens, user })
  }, [])

  useEffect(() => {
    bindAuthHandlers(
      () => {
        persist(null, null)
        qc.clear()
      },
      (t) => {
        setState((s) => {
          writeStored({ tokens: t, user: s.user })
          return { ...s, tokens: t }
        })
      },
    )
  }, [persist, qc])

  const requestOtp = useCallback(
    (phone: string) =>
      api<{ detail?: string; debug_code?: string | null; retry_after?: number }>('/auth/otp/request', {
        method: 'POST',
        body: { phone },
        auth: false,
      }),
    [],
  )

  const verifyOtp = useCallback(
    async (phone: string, code: string) => {
      const res = await api<LoginOut>('/auth/otp/verify', { method: 'POST', body: { phone, code }, auth: false })
      qc.clear()
      const tokens = { access: res.access, refresh: res.refresh }
      setTokens(tokens) // /users/me shu token bilan
      const user = toUser(res.user, await fullName())
      persist(tokens, user)
      return { is_new_user: res.is_new_user, user }
    },
    [persist, qc],
  )

  const refreshUser = useCallback(async () => {
    const tokens = getTokens()
    if (!tokens) return null
    // Rollar access token'da — yangi rol (doctor) uchun avval refresh
    const t = await api<Tokens>('/auth/token/refresh', { method: 'POST', body: { refresh: tokens.refresh }, auth: false })
    setTokens(t)
    const me = await api<AuthMe>('/auth/me')
    const user = toUser(me, await fullName())
    persist(t, user)
    return user
  }, [persist])

  const switchRole = useCallback(
    async (role: Role) => {
      if (!state.tokens || !state.user) return
      const t = await api<Tokens>('/auth/switch-role', { method: 'POST', body: { refresh: state.tokens.refresh, role } })
      // Rol almashganda eski cache boshqa rolga tegishli — butunlay tozalanadi
      qc.clear()
      // Backend rolni bazadan qayta tekshiradi — muvaffaqiyat = rol haqiqatan bor
      persist(t, {
        ...state.user,
        role,
        active_role: role,
        available_roles: Array.from(new Set([...(state.user.available_roles ?? []), role])),
      })
    },
    [persist, qc, state.tokens, state.user],
  )

  const updateUser = useCallback(
    (patch: Partial<AuthUser>) => {
      if (!state.user) return
      persist(state.tokens, { ...state.user, ...patch })
    },
    [persist, state.tokens, state.user],
  )

  const logout = useCallback(
    async (all = false) => {
      try {
        if (state.tokens) {
          if (all) await api('/auth/logout/all', { method: 'POST' })
          else await api('/auth/logout', { method: 'POST', body: { refresh: state.tokens.refresh } })
        }
      } catch {
        /* chiqishda xato bo'lsa ham lokal sessiya tozalanadi */
      }
      persist(null, null)
      qc.clear()
    },
    [persist, qc, state.tokens],
  )

  const value = useMemo<AuthCtx>(
    () => ({
      user: state.user,
      isAuthed: !!state.tokens && !!state.user,
      requestOtp,
      verifyOtp,
      switchRole,
      updateUser,
      refreshUser,
      logout,
    }),
    [state, requestOtp, verifyOtp, switchRole, updateUser, refreshUser, logout],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth() {
  const c = useContext(Ctx)
  if (!c) throw new Error('useAuth must be used inside AuthProvider')
  return c
}
