import type { Tokens } from './types'

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api/v1'
const STORAGE_KEY = 'turon.auth'

export class ApiError extends Error {
  status: number
  detail: string
  fields: Record<string, string>
  data: unknown
  constructor(status: number, data: unknown) {
    const { detail, fields } = parseApiError(status, data)
    super(detail)
    this.status = status
    this.detail = detail
    this.fields = fields
    this.data = data
  }
}

const STATUS_TEXT: Record<number, string> = {
  0: "Internet aloqasi yo'q. Tarmoqni tekshirib, qayta urinib ko'ring.",
  401: 'Sessiya tugadi. Iltimos, qayta kiring.',
  403: "Bu amal uchun ruxsat yo'q.",
  404: 'Topilmadi.',
  409: "Bu amalni hozir bajarib bo'lmaydi.",
  429: "Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring.",
  500: "Serverda xatolik. Birozdan so'ng urinib ko'ring.",
  503: "Xizmat vaqtincha ishlamayapti. Birozdan so'ng urinib ko'ring.",
}

/** `{detail}` va `{field: [...]}` ikkala shaklni bitta ko'rinishga keltiradi. */
export function parseApiError(status: number, data: unknown): { detail: string; fields: Record<string, string> } {
  const fields: Record<string, string> = {}
  let detail = ''
  if (data && typeof data === 'object' && !Array.isArray(data)) {
    for (const [k, v] of Object.entries(data as Record<string, unknown>)) {
      const msg = Array.isArray(v) ? v.map(String).join(' ') : typeof v === 'string' ? v : ''
      if (!msg) continue
      if (k === 'detail' || k === 'non_field_errors') detail = msg
      else fields[k] = msg
    }
  } else if (Array.isArray(data)) {
    detail = data.map(String).join(' ')
  }
  if (!detail) {
    detail = Object.values(fields)[0] || STATUS_TEXT[status] || (status >= 500 ? STATUS_TEXT[500] : 'Xatolik yuz berdi')
  }
  return { detail, fields }
}

export function errText(e: unknown): string {
  if (e instanceof ApiError) return e.detail
  if (e instanceof Error) return e.message
  return 'Xatolik yuz berdi'
}

// ---- token saqlash ---------------------------------------------------------
export type Stored = { tokens: Tokens | null; user: unknown }

export function readStored(): Stored {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return JSON.parse(raw)
  } catch {
    /* storage bloklangan bo'lishi mumkin */
  }
  return { tokens: null, user: null }
}

export function writeStored(s: Stored) {
  try {
    if (!s.tokens) localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, JSON.stringify(s))
  } catch {
    /* ignore */
  }
}

let tokens: Tokens | null = readStored().tokens
let onAuthLost: (() => void) | null = null
let onTokens: ((t: Tokens) => void) | null = null

export function setTokens(t: Tokens | null) {
  tokens = t
}
export function getTokens() {
  return tokens
}
export function bindAuthHandlers(lost: () => void, updated: (t: Tokens) => void) {
  onAuthLost = lost
  onTokens = updated
}

// Bir vaqtda bir nechta 401 kelsa — bitta refresh, qolganlari shu promise'ni kutadi.
let refreshing: Promise<boolean> | null = null
async function refreshTokens(): Promise<boolean> {
  if (!tokens?.refresh) return false
  if (!refreshing) {
    const current = tokens.refresh
    refreshing = (async () => {
      try {
        const res = await fetch(`${BASE}/accounts/auth/token/refresh`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh: current }),
        })
        if (!res.ok) return false
        const data = await res.json()
        tokens = { access: data.access, refresh: data.refresh || current }
        onTokens?.(tokens)
        return true
      } catch {
        return false
      } finally {
        setTimeout(() => {
          refreshing = null
        }, 0)
      }
    })()
  }
  return refreshing
}

export type Query = Record<string, string | number | boolean | null | undefined>

export interface RequestOpts {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  query?: Query
  headers?: Record<string, string>
  auth?: boolean
  signal?: AbortSignal
}

function buildUrl(path: string, query?: Query) {
  const url = `${BASE}${path}`
  if (!query) return url
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '') continue
    qs.set(k, String(v))
  }
  const s = qs.toString()
  return s ? `${url}?${s}` : url
}

export async function api<T = unknown>(path: string, opts: RequestOpts = {}): Promise<T> {
  const { method = 'GET', body, query, headers = {}, auth = true, signal } = opts
  const isForm = typeof FormData !== 'undefined' && body instanceof FormData

  const doFetch = () => {
    const h: Record<string, string> = { Accept: 'application/json', 'Accept-Language': 'uz', ...headers }
    if (!isForm && body !== undefined) h['Content-Type'] = 'application/json'
    if (auth && tokens?.access) h.Authorization = `Bearer ${tokens.access}`
    return fetch(buildUrl(path, query), {
      method,
      headers: h,
      body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
      signal,
    })
  }

  let res: Response
  try {
    res = await doFetch()
    if (res.status === 401 && auth && tokens?.refresh) {
      const ok = await refreshTokens()
      if (ok) res = await doFetch()
    }
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new ApiError(0, null)
  }

  if (res.status === 204) return undefined as T
  const ct = res.headers.get('content-type') || ''
  const data = ct.includes('json') ? await res.json().catch(() => null) : await res.text().catch(() => null)
  if (!res.ok) {
    if (res.status === 401 && auth && tokens) onAuthLost?.()
    throw new ApiError(res.status, data)
  }
  return data as T
}

/** Ro'yxat endpointlari modulga qarab massiv yoki `{results}` qaytaradi. */
export function asList<T>(data: unknown, key = 'results'): T[] {
  if (Array.isArray(data)) return data as T[]
  if (data && typeof data === 'object') {
    const d = data as Record<string, unknown>
    for (const k of [key, 'results', 'items', 'data']) if (Array.isArray(d[k])) return d[k] as T[]
  }
  return []
}

export function uuid(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}
