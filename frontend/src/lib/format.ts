import { formatInTimeZone, toZonedTime } from 'date-fns-tz'

export const TZ = 'Asia/Tashkent'

const MONTHS = ['yanvar', 'fevral', 'mart', 'aprel', 'may', 'iyun', 'iyul', 'avgust', 'sentabr', 'oktabr', 'noyabr', 'dekabr']
const MONTHS_SHORT = ['yan', 'fev', 'mar', 'apr', 'may', 'iyn', 'iyl', 'avg', 'sen', 'okt', 'noy', 'dek']
export const WEEKDAYS = ['Dushanba', 'Seshanba', 'Chorshanba', 'Payshanba', 'Juma', 'Shanba', 'Yakshanba']
export const WEEKDAYS_SHORT = ['Du', 'Se', 'Ch', 'Pa', 'Ju', 'Sh', 'Ya']

/** Decimal string → "150 000 so'm". Float'ga aylantirilmaydi — butun qism matn sifatida guruhlanadi. */
export function money(value: string | number | null | undefined, withCurrency = true): string {
  if (value === null || value === undefined || value === '') return '—'
  const s = String(value)
  const neg = s.startsWith('-')
  const [intPart, frac = ''] = s.replace('-', '').split('.')
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ' ')
  const tail = frac && /[1-9]/.test(frac) ? ',' + frac.replace(/0+$/, '') : ''
  return `${neg ? '−' : ''}${grouped}${tail}${withCurrency ? " so'm" : ''}`
}

/** Decimal stringlarni float'siz qo'shish (tiyinda). */
export function sumMoney(values: string[]): string {
  let tiyin = 0n
  for (const v of values) {
    const neg = v.startsWith('-')
    const [i, f = ''] = v.replace('-', '').split('.')
    const t = BigInt(i || '0') * 100n + BigInt((f + '00').slice(0, 2))
    tiyin += neg ? -t : t
  }
  const neg = tiyin < 0n
  const abs = neg ? -tiyin : tiyin
  return `${neg ? '-' : ''}${abs / 100n}.${String(abs % 100n).padStart(2, '0')}`
}

export function tz(iso: string | Date) {
  return toZonedTime(iso, TZ)
}

function asDate(iso: string) {
  return iso.length === 10 ? new Date(iso + 'T12:00:00') : tz(iso)
}

export function todayISO(offsetDays = 0): string {
  return formatInTimeZone(new Date(Date.now() + offsetDays * 86400000), TZ, 'yyyy-MM-dd')
}

export function time(iso?: string | null) {
  return iso ? formatInTimeZone(iso, TZ, 'HH:mm') : '—'
}

export function dateLong(iso?: string | null) {
  if (!iso) return '—'
  const d = asDate(iso)
  const yr = d.getFullYear() !== new Date().getFullYear() ? `, ${d.getFullYear()}` : ''
  return `${d.getDate()}-${MONTHS[d.getMonth()]}${yr}`
}

export function dateShort(iso: string) {
  const d = asDate(iso)
  return `${d.getDate()} ${MONTHS_SHORT[d.getMonth()]}`
}

export function monthName(iso: string) {
  const d = asDate(iso)
  return `${MONTHS[d.getMonth()][0].toUpperCase()}${MONTHS[d.getMonth()].slice(1)} ${d.getFullYear()}`
}

export function weekday(iso: string) {
  return WEEKDAYS[(asDate(iso).getDay() + 6) % 7]
}

export function weekdayShort(iso: string) {
  return WEEKDAYS_SHORT[(asDate(iso).getDay() + 6) % 7]
}

export function dayNum(iso: string) {
  return asDate(iso).getDate()
}

/** "Bugun, 14:00" / "Ertaga, 09:30" / "12-oktabr, 10:00" */
export function relativeDateTime(iso?: string | null) {
  if (!iso) return '—'
  const day = formatInTimeZone(iso, TZ, 'yyyy-MM-dd')
  const t = time(iso)
  if (day === todayISO()) return `Bugun, ${t}`
  if (day === todayISO(1)) return `Ertaga, ${t}`
  if (day === todayISO(-1)) return `Kecha, ${t}`
  return `${dateLong(iso)}, ${t}`
}

export function relativeDay(dateISO: string) {
  if (dateISO === todayISO()) return 'Bugun'
  if (dateISO === todayISO(1)) return 'Ertaga'
  return weekdayShort(dateISO)
}

export function dayKey(iso: string) {
  return formatInTimeZone(iso, TZ, 'yyyy-MM-dd')
}

export function addDaysISO(dateISO: string, n: number) {
  const d = new Date(dateISO + 'T12:00:00Z')
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}

export function ageFrom(birth: string) {
  const b = new Date(birth)
  const n = new Date()
  let a = n.getFullYear() - b.getFullYear()
  if (n.getMonth() < b.getMonth() || (n.getMonth() === b.getMonth() && n.getDate() < b.getDate())) a--
  return a
}

/** "+998901234567" → "+998 90 123-45-67" */
export function phone(p?: string | null) {
  if (!p) return '—'
  const d = p.replace(/\D/g, '')
  if (d.length === 12 && d.startsWith('998')) return `+998 ${d.slice(3, 5)} ${d.slice(5, 8)}-${d.slice(8, 10)}-${d.slice(10)}`
  return p
}

export function initials(name?: string | null) {
  if (!name) return '?'
  return (
    name
      .trim()
      .split(/\s+/)
      .slice(0, 2)
      .map((w) => w[0]?.toUpperCase())
      .join('') || '?'
  )
}

export function minutes(m: number) {
  if (m < 60) return `${m} daq`
  const h = Math.floor(m / 60)
  return m % 60 ? `${h} soat ${m % 60} daq` : `${h} soat`
}

export function rating(r: number | string | null | undefined) {
  if (r === null || r === undefined || r === '') return null
  const n = typeof r === 'string' ? parseFloat(r) : r
  return Number.isFinite(n) && n > 0 ? n.toFixed(1) : null
}
