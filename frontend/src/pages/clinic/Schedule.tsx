import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CalendarClock } from 'lucide-react'
import { time, todayISO, tz } from '@/lib/format'
import { Avatar, EmptyState, ErrorState, PageHeader, Skeleton, cx } from '@/components/ui'
import { useClinic } from './ctx'
import { BLOCK_REASON, DateNav, SLOT_STATUS } from './shared'
import type { ScheduleColumn } from './types'

const PX_PER_MIN = 1.7

function minuteOfDay(iso: string) {
  const d = tz(iso)
  return d.getHours() * 60 + d.getMinutes()
}

export default function Schedule() {
  const { capi, clinicId } = useClinic()
  const [date, setDate] = useState(todayISO())
  const q = useQuery({
    queryKey: ['clinic', clinicId, 'schedule', date],
    queryFn: () => capi<ScheduleColumn[]>('/clinic/schedule', { query: { date } }),
    placeholderData: (prev) => prev,
  })

  const cols = useMemo(() => (q.data ?? []).filter((c) => c.slots.length > 0), [q.data])
  const range = useMemo(() => {
    let lo = Infinity
    let hi = -Infinity
    for (const c of cols)
      for (const s of c.slots) {
        lo = Math.min(lo, minuteOfDay(s.start_at))
        hi = Math.max(hi, minuteOfDay(s.end_at) || 24 * 60)
      }
    if (!Number.isFinite(lo)) return null
    return { start: Math.floor(lo / 60) * 60, end: Math.ceil(hi / 60) * 60 }
  }, [cols])

  const stats = useMemo(() => {
    const out: Record<string, number> = { free: 0, held: 0, booked: 0, blocked: 0 }
    for (const c of cols) for (const s of c.slots) out[s.status] = (out[s.status] ?? 0) + 1
    return out
  }, [cols])

  return (
    <div>
      <PageHeader title="Umumiy jadval" subtitle="Shifokorlar bo'yicha kunlik ko'rinish — band, bo'sh va yopiq vaqtlar" />
      <div className="card mb-5 flex flex-col gap-3 p-3 lg:flex-row lg:items-center lg:justify-between">
        <DateNav value={date} onChange={setDate} className="flex-wrap" />
        <div className="flex flex-wrap gap-2">
          {Object.entries(SLOT_STATUS).map(([k, v]) => (
            <span key={k} className="chip bg-ink-50 text-ink-600">
              <span className={cx('h-2.5 w-2.5 rounded-full', v.dot)} />
              {v.label} <b className="text-ink-900">{stats[k] ?? 0}</b>
            </span>
          ))}
        </div>
      </div>

      {q.isLoading ? (
        <div className="card grid grid-cols-3 gap-4 p-5">
          {[0, 1, 2].map((i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="h-10" />
              <Skeleton className="h-24" />
              <Skeleton className="h-16" />
              <Skeleton className="h-24" />
            </div>
          ))}
        </div>
      ) : q.error ? (
        <div className="card"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
      ) : !range ? (
        <div className="card">
          <EmptyState icon={<CalendarClock className="h-7 w-7" />} title="Bu kunda jadval yo'q" text="Klinika yopiq yoki shifokorlar uchun ish qoidasi kiritilmagan." />
        </div>
      ) : (
        <div className={cx('card overflow-hidden', q.isFetching && 'opacity-70')}>
          <div className="overflow-x-auto">
            <div className="flex" style={{ minWidth: 64 + cols.length * 170 }}>
              {/* Vaqt o'qi */}
              <div className="sticky left-0 z-10 w-16 shrink-0 border-r border-ink-100 bg-white">
                <div className="h-16 border-b border-ink-100" />
                <div className="relative" style={{ height: (range.end - range.start) * PX_PER_MIN }}>
                  {Array.from({ length: (range.end - range.start) / 60 + 1 }).map((_, i) => (
                    <div key={i} className="absolute right-2 -translate-y-1/2 text-xs font-semibold tabular-nums text-ink-400" style={{ top: i * 60 * PX_PER_MIN }}>
                      {String(range.start / 60 + i).padStart(2, '0')}:00
                    </div>
                  ))}
                </div>
              </div>
              {cols.map((c) => (
                <div key={c.doctor_id} className="min-w-[170px] flex-1 border-r border-ink-100 last:border-r-0">
                  <div className="flex h-16 items-center gap-2.5 border-b border-ink-100 px-3">
                    <Avatar name={c.doctor} size={34} className="rounded-xl" />
                    <div className="min-w-0">
                      <div className="truncate text-sm font-bold text-ink-900">{c.doctor}</div>
                      <div className="text-xs text-ink-400">{c.slots.filter((s) => s.status === 'booked').length} / {c.slots.length} band</div>
                    </div>
                  </div>
                  <div className="relative" style={{ height: (range.end - range.start) * PX_PER_MIN }}>
                    {Array.from({ length: (range.end - range.start) / 60 }).map((_, i) => (
                      <div key={i} className="absolute inset-x-0 border-t border-dashed border-ink-100" style={{ top: i * 60 * PX_PER_MIN }} />
                    ))}
                    {c.slots.map((s) => {
                      const top = (minuteOfDay(s.start_at) - range.start) * PX_PER_MIN
                      const h = Math.max(18, ((minuteOfDay(s.end_at) || 24 * 60) - minuteOfDay(s.start_at)) * PX_PER_MIN - 3)
                      const st = SLOT_STATUS[s.status] ?? SLOT_STATUS.free
                      const title = [`${time(s.start_at)}–${time(s.end_at)}`, st.label, s.block_reason && BLOCK_REASON[s.block_reason], s.room].filter(Boolean).join(' · ')
                      return (
                        <div
                          key={s.slot_id}
                          title={title}
                          className={cx('absolute inset-x-1.5 overflow-hidden rounded-xl px-2 py-1 text-[11px] leading-tight ring-1 ring-inset transition hover:z-10 hover:shadow-lift', st.cls)}
                          style={{ top: top + 1.5, height: h }}
                        >
                          <div className="font-bold tabular-nums">{time(s.start_at)}</div>
                          {h > 34 && <div className="truncate opacity-80">{s.block_reason ? BLOCK_REASON[s.block_reason] ?? st.label : s.room ?? st.label}</div>}
                        </div>
                      )
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
