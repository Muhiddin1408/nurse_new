import { useEffect, useMemo, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CalendarX2, ChevronLeft, ChevronRight, Moon, Sun, Sunrise } from 'lucide-react'
import { api } from '@/lib/api'
import { addDaysISO, dateShort, dayKey, dayNum, relativeDay, time, todayISO, tz } from '@/lib/format'
import type { Slot } from '@/lib/types'
import { Skeleton, cx } from './ui'

export type Place = 'clinic' | 'home'
export interface PlacedSlot extends Slot {
  clinic_id?: string | null
}

const DAYS = 21

function matches(s: PlacedSlot, place?: Place, clinicId?: string) {
  if (!place) return true
  if (place === 'home') return !s.clinic_id
  if (!s.clinic_id) return false
  return !clinicId || s.clinic_id === clinicId
}

/** Birinchi bo'sh kunni topish uchun (oraliq so'rovi 200 slot bilan cheklangan). */
export function useFirstFreeDay(doctorId?: string, place?: Place, clinicId?: string) {
  return useQuery({
    queryKey: ['slots-range', doctorId, todayISO()],
    queryFn: () => api<PlacedSlot[]>(`/schedule/doctors/${doctorId}/slots`, { auth: false, query: { date_from: todayISO(), date_to: addDaysISO(todayISO(), DAYS - 1) } }),
    enabled: !!doctorId,
    staleTime: 30_000,
    select: (rows) => rows.find((s) => matches(s, place, clinicId)) ?? null,
  })
}

export function useDaySlots(doctorId: string | undefined, date: string) {
  return useQuery({
    queryKey: ['slots', doctorId, date],
    queryFn: () => api<PlacedSlot[]>(`/schedule/doctors/${doctorId}/slots`, { auth: false, query: { date } }),
    enabled: !!doctorId && !!date,
    staleTime: 15_000,
  })
}

export function SlotPicker({
  doctorId,
  date,
  onDate,
  selected,
  onSelect,
  place,
  clinicId,
  clinicNames,
  compact,
}: {
  doctorId: string
  date: string
  onDate: (d: string) => void
  selected?: string | null
  onSelect: (s: PlacedSlot) => void
  place?: Place
  clinicId?: string
  clinicNames?: Record<string, string>
  compact?: boolean
}) {
  const days = useMemo(() => Array.from({ length: DAYS }, (_, i) => addDaysISO(todayISO(), i)), [])
  const stripRef = useRef<HTMLDivElement>(null)
  const q = useDaySlots(doctorId, date)
  const slots = useMemo(() => (q.data ?? []).filter((s) => matches(s, place, clinicId)), [q.data, place, clinicId])

  useEffect(() => {
    // scrollIntoView sahifani ham vertikal aylantiradi — faqat tasmaning o'zini suramiz
    const strip = stripRef.current
    const el = strip?.querySelector<HTMLElement>(`[data-day="${date}"]`)
    if (strip && el) strip.scrollTo({ left: el.offsetLeft - strip.clientWidth / 2 + el.clientWidth / 2, behavior: 'smooth' })
  }, [date])

  const groups = useMemo(() => {
    const g: { key: string; label: string; icon: typeof Sun; items: PlacedSlot[] }[] = [
      { key: 'm', label: 'Ertalab', icon: Sunrise, items: [] },
      { key: 'd', label: 'Kunduzi', icon: Sun, items: [] },
      { key: 'e', label: 'Kechqurun', icon: Moon, items: [] },
    ]
    for (const s of slots) {
      const h = tz(s.start_at).getHours()
      g[h < 12 ? 0 : h < 17 ? 1 : 2].items.push(s)
    }
    return g.filter((x) => x.items.length)
  }, [slots])

  const scroll = (dir: number) => stripRef.current?.scrollBy({ left: dir * 240, behavior: 'smooth' })
  const multipleClinics = clinicNames && new Set(slots.map((s) => s.clinic_id).filter(Boolean)).size > 1

  return (
    <div className="min-w-0">
      <div className="relative">
        <button onClick={() => scroll(-1)} className="absolute -left-2 top-1/2 z-10 hidden -translate-y-1/2 rounded-full bg-white p-1.5 shadow-soft ring-1 ring-ink-100 sm:block">
          <ChevronLeft className="h-4 w-4" />
        </button>
        <div ref={stripRef} className="relative flex gap-2 overflow-x-auto pb-2 scrollbar-none sm:px-4">
          {days.map((d) => (
            <button
              key={d}
              data-day={d}
              onClick={() => onDate(d)}
              className={cx(
                'flex min-w-[64px] flex-col items-center rounded-2xl px-2 py-2.5 transition',
                d === date ? 'bg-ink-900 text-white shadow-lift' : 'bg-ink-50 text-ink-700 hover:bg-ink-100',
              )}
            >
              <span className={cx('text-[11px] font-semibold uppercase', d === date ? 'text-white/60' : 'text-ink-400')}>{relativeDay(d)}</span>
              <span className="text-lg font-extrabold leading-tight">{dayNum(d)}</span>
              <span className={cx('text-[11px]', d === date ? 'text-white/60' : 'text-ink-400')}>{dateShort(d).split(' ')[1]}</span>
            </button>
          ))}
        </div>
        <button onClick={() => scroll(1)} className="absolute -right-2 top-1/2 z-10 hidden -translate-y-1/2 rounded-full bg-white p-1.5 shadow-soft ring-1 ring-ink-100 sm:block">
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>

      <div className={cx('mt-4', compact ? 'min-h-[120px]' : 'min-h-[160px]')}>
        {q.isLoading ? (
          <div className="grid grid-cols-4 gap-2 sm:grid-cols-5">
            {Array.from({ length: 10 }).map((_, i) => (
              <Skeleton key={i} className="h-11 rounded-xl" />
            ))}
          </div>
        ) : groups.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-2xl bg-ink-50 px-4 py-8 text-center">
            <CalendarX2 className="h-8 w-8 text-ink-300" />
            <div className="mt-2 font-semibold text-ink-700">Bu kunda bo'sh vaqt yo'q</div>
            <div className="text-sm text-ink-500">Boshqa kunni tanlang</div>
          </div>
        ) : (
          <div className="space-y-4">
            {groups.map((g) => (
              <div key={g.key}>
                <div className="mb-2 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-ink-400">
                  <g.icon className="h-3.5 w-3.5" /> {g.label} <span className="font-semibold normal-case tracking-normal text-ink-300">· {g.items.length}</span>
                </div>
                <div className={cx('grid gap-2', compact ? 'grid-cols-4' : 'grid-cols-3 sm:grid-cols-4 md:grid-cols-5')}>
                  {g.items.map((s) => (
                    <button
                      key={s.id}
                      onClick={() => onSelect(s)}
                      title={s.clinic_id && clinicNames ? clinicNames[s.clinic_id] : s.clinic_id ? undefined : 'Uyga chaqiruv'}
                      className={cx(
                        'relative rounded-xl py-2.5 text-[15px] font-bold tabular-nums transition',
                        selected === s.id ? 'bg-brand-500 text-white shadow-glow' : 'bg-brand-50 text-brand-800 hover:bg-brand-100',
                      )}
                    >
                      {time(s.start_at)}
                      {multipleClinics && s.clinic_id && (
                        <span className={cx('block text-[10px] font-semibold leading-tight', selected === s.id ? 'text-white/80' : 'text-brand-600/70')}>
                          {clinicNames![s.clinic_id]?.replace('Turon Clinic ', '')}
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export function slotDay(s: Slot) {
  return dayKey(s.start_at)
}
