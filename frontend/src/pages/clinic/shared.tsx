import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, CalendarDays } from 'lucide-react'
import { addDaysISO, dateLong, relativeDay, todayISO, weekday } from '@/lib/format'
import { Button, cx } from '@/components/ui'

export const AFFILIATION_STATUS = {
  invited: { label: 'Taklif yuborilgan', tone: 'amber' as const },
  active: { label: 'Faol', tone: 'green' as const },
  paused: { label: "To'xtatilgan", tone: 'gray' as const },
  ended: { label: 'Tugatilgan', tone: 'red' as const },
  declined: { label: 'Rad etgan', tone: 'red' as const },
}

export const PAYMENT_MODE: Record<string, string> = {
  prepaid: "Oldindan to'langan",
  deposit: 'Depozit',
  at_clinic: 'Klinikada',
}

export const SLOT_STATUS: Record<string, { label: string; cls: string; dot: string }> = {
  free: { label: "Bo'sh", cls: 'bg-brand-50 text-brand-700 ring-brand-200', dot: 'bg-brand-400' },
  held: { label: "To'lov kutilmoqda", cls: 'bg-amber-50 text-amber-800 ring-amber-200', dot: 'bg-amber-400' },
  booked: { label: 'Band', cls: 'bg-sky-500 text-white ring-sky-600', dot: 'bg-sky-500' },
  blocked: { label: 'Yopiq', cls: 'bg-ink-100 text-ink-500 ring-ink-200 bg-[repeating-linear-gradient(135deg,transparent,transparent_6px,rgba(100,117,150,.12)_6px,rgba(100,117,150,.12)_12px)]', dot: 'bg-ink-300' },
}

export const BLOCK_REASON: Record<string, string> = {
  travel_buffer: "Yo'l vaqti",
  time_off: "Ta'til",
  manual: "Qo'lda yopilgan",
  clinic_closed: 'Klinika yopiq',
}

export const WEEKDAY_NAMES = ['Dushanba', 'Seshanba', 'Chorshanba', 'Payshanba', 'Juma', 'Shanba', 'Yakshanba']

export function hhmm(t?: string | null) {
  return t ? t.slice(0, 5) : '—'
}

/** Kun tanlagich: ‹ Bugun › + sana input */
export function DateNav({ value, onChange, className }: { value: string; onChange: (d: string) => void; className?: string }) {
  return (
    <div className={cx('flex items-center gap-2', className)}>
      <Button variant="outline" size="sm" className="!px-2.5" onClick={() => onChange(addDaysISO(value, -1))} aria-label="Oldingi kun">
        <ChevronLeft className="h-4 w-4" />
      </Button>
      <label className="relative flex h-9 cursor-pointer items-center gap-2 rounded-xl border border-ink-200 bg-white px-3 text-sm font-semibold text-ink-800 hover:border-ink-300">
        <CalendarDays className="h-4 w-4 text-brand-600" />
        <span className="whitespace-nowrap">
          {relativeDay(value) === 'Bugun' || relativeDay(value) === 'Ertaga' ? `${relativeDay(value)}, ` : `${weekday(value)}, `}
          {dateLong(value)}
        </span>
        <input type="date" value={value} onChange={(e) => e.target.value && onChange(e.target.value)} className="absolute inset-0 cursor-pointer opacity-0" />
      </label>
      <Button variant="outline" size="sm" className="!px-2.5" onClick={() => onChange(addDaysISO(value, 1))} aria-label="Keyingi kun">
        <ChevronRight className="h-4 w-4" />
      </Button>
      {value !== todayISO() && (
        <Button variant="ghost" size="sm" onClick={() => onChange(todayISO())}>
          Bugun
        </Button>
      )}
    </div>
  )
}

/** "+998" dan keyingi 9 raqamni "90 123 45 67" ko'rinishida */
export function formatPhoneDigits(d: string) {
  const s = d.replace(/\D/g, '').slice(0, 9)
  return [s.slice(0, 2), s.slice(2, 5), s.slice(5, 7), s.slice(7, 9)].filter(Boolean).join(' ')
}

export function PhoneField({ value, onChange, autoFocus }: { value: string; onChange: (digits: string) => void; autoFocus?: boolean }) {
  return (
    <div className="flex items-center rounded-2xl border border-ink-200 bg-white transition focus-within:border-brand-500 focus-within:ring-4 focus-within:ring-brand-500/15">
      <span className="pl-4 pr-2 text-[15px] font-semibold text-ink-500">+998</span>
      <input
        autoFocus={autoFocus}
        inputMode="numeric"
        className="w-full rounded-2xl bg-transparent py-3 pr-4 text-[15px] tracking-wide text-ink-900 outline-none placeholder:text-ink-400"
        placeholder="90 123 45 67"
        value={formatPhoneDigits(value)}
        onChange={(e) => onChange(e.target.value.replace(/\D/g, '').slice(0, 9))}
      />
    </div>
  )
}

export function downloadCsv(filename: string, rows: (string | number)[][]) {
  const esc = (v: string | number) => {
    const s = String(v ?? '')
    return /[",;\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const csv = '﻿' + rows.map((r) => r.map(esc).join(',')).join('\n')
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function useDebounced<T>(value: T, ms = 300) {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}
