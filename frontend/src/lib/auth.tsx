import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, bindAuthHandlers, readStored, setTokens, writeStored } from './api'
import type { AuthUser, Role, Tokens } from './types'

interface VerifyResponse {
  tokens: Tokens
  is_new_user: boolean
  user: AuthUser
}

interface AuthCtx {
  user: AuthUser | null
  isAuthed: boolean
  requestOtp: (phone: string) => Promise<{ detail?: string; debug_code?: string }>
  verifyOtp: (phone: string, code: string) => Promise<VerifyResponse>
  switchRole: (role: Role) => Promise<void>
  updateUser: (patch: Partial<AuthUser>) => void
  logout: (all?: boolean) => Promise<void>
}

const Ctx = createContext<AuthCtx | null>(null)

export const ROLE_LABEL: Record<Role, string> = {
  client: 'Mijoz',
  doctor: 'Shifokor',
  clinic_admin: 'Klinika admini',
  platform_admin: 'Moderator',
}

export const ROLE_HOME: Record<Role, string> = {
  client: '/account',
  doctor: '/doctor',
  clinic_admin: '/clinic',
  platform_admin: '/admin',
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
    (phone: string) => api<{ detail?: string; debug_code?: string }>('/accounts/auth/otp/request', { method: 'POST', body: { phone }, auth: false }),
    [],
  )

  const verifyOtp = useCallback(
    async (phone: string, code: string) => {
      const res = await api<VerifyResponse>('/accounts/auth/otp/verify', { method: 'POST', body: { phone, code }, auth: false })
      qc.clear()
      persist(res.tokens, res.user)
      return res
    },
    [persist, qc],
  )

  const switchRole = useCallback(
    async (role: Role) => {
      if (!state.tokens || !state.user) return
      const t = await api<Tokens>('/accounts/auth/switch-role', { method: 'POST', body: { refresh: state.tokens.refresh, role } })
      // Rol almashganda eski cache boshqa rolga tegishli — butunlay tozalanadi
      qc.clear()
      // Backend rolni bazadan qayta tekshiradi — muvaffaqiyat = rol haqiqatan bor
      persist(t, { ...state.user, active_role: role, available_roles: Array.from(new Set([...(state.user.available_roles ?? []), role])) })
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
          if (all) await api('/accounts/auth/logout/all', { method: 'POST' })
          else await api('/accounts/auth/logout', { method: 'POST', body: { refresh: state.tokens.refresh } })
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
    () => ({ user: state.user, isAuthed: !!state.tokens && !!state.user, requestOtp, verifyOtp, switchRole, updateUser, logout }),
    [state, requestOtp, verifyOtp, switchRole, updateUser, logout],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth() {
  const c = useContext(Ctx)
  if (!c) throw new Error('useAuth must be used inside AuthProvider')
  return c
}
