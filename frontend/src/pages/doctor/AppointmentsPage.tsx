import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CalendarSearch, ChevronLeft, ChevronRight, Home, MapPin } from 'lucide-react'
import { api } from '@/lib/api'
import { addDaysISO, dateLong, dateShort, dayKey, time, todayISO, weekday } from '@/lib/format'
import { Avatar, Button, EmptyState, ErrorState, ListSkeleton, PageHeader, Segmented, StatusBadge } from '@/components/ui'
import { AppointmentActions, AppointmentDetailModal, patientLine } from './appointments-ui'
import { APPT_STATUS, type Appointment } from './shared'

function mondayOf(iso: string) {
  const d = new Date(iso + 'T12:00:00Z')
  const wd = (d.getUTCDay() + 6) % 7
  return addDaysISO(iso, -wd)
}

type Range = 'week' | 'month'
type StatusFilter = 'all' | 'confirmed' | 'completed' | 'no_show' | 'cancelled'

export default function AppointmentsPage() {
  const [range, setRange] = useState<Range>('week')
  const [anchor, setAnchor] = useState(() => mondayOf(todayISO()))
  const [status, setStatus] = useState<StatusFilter>('all')
  const [openId, setOpenId] = useState<string | null>(null)

  const span = range === 'week' ? 7 : 30 // backend: oraliq 31 kundan oshmasin
  const from = anchor
  const to = addDaysISO(anchor, span - 1)

  const q = useQuery({
    queryKey: ['doctor', 'appointments', 'list', from, to, status],
    queryFn: () => api<Appointment[]>('/doctor/appointments', { query: { from, to, status: status === 'all' ? undefined : status } }),
  })

  const groups = useMemo(() => {
    const m = new Map<string, Appointment[]>()
    for (const a of [...(q.data ?? [])].sort((x, y) => x.start_at.localeCompare(y.start_at))) {
      const k = dayKey(a.start_at)
      m.set(k, [...(m.get(k) ?? []), a])
    }
    return [...m.entries()]
  }, [q.data])

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Qabullar" subtitle="Barcha qabullaringiz — kun bo'yicha guruhlangan" />

      <div className="card mb-5 flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setAnchor(addDaysISO(anchor, -span))} aria-label="Oldingi">
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <div className="min-w-[170px] text-center text-sm font-bold text-ink-800">
            {dateShort(from)} — {dateShort(to)}
          </div>
          <Button size="sm" variant="outline" onClick={() => setAnchor(addDaysISO(anchor, span))} aria-label="Keyingi">
            <ChevronRight className="h-4 w-4" />
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setAnchor(range === 'week' ? mondayOf(todayISO()) : todayISO())}>
            Bugun
          </Button>
        </div>
        <Segmented
          size="sm"
          value={range}
          onChange={(r) => {
            setRange(r)
            setAnchor(r === 'week' ? mondayOf(todayISO()) : addDaysISO(todayISO(), -14))
          }}
          options={[
            { value: 'week', label: 'Hafta' },
            { value: 'month', label: '30 kun' },
          ]}
        />
      </div>

      <div className="scrollbar-none mb-5 overflow-x-auto">
        <Segmented
          size="sm"
          value={status}
          onChange={setStatus}
          options={[
            { value: 'all', label: 'Hammasi' },
            { value: 'confirmed', label: 'Kutilmoqda' },
            { value: 'completed', label: 'Yakunlangan' },
            { value: 'no_show', label: 'Kelmadi' },
            { value: 'cancelled', label: 'Bekor' },
          ]}
        />
      </div>

      {q.isLoading && <ListSkeleton rows={5} />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && groups.length === 0 && (
        <div className="card">
          <EmptyState icon={<CalendarSearch className="h-7 w-7" />} title="Bu davrda qabul yo'q" text="Boshqa sanalarni tanlab ko'ring yoki filtrni o'zgartiring." />
        </div>
      )}
      <div className="space-y-6">
        {groups.map(([day, items]) => (
          <div key={day}>
            <div className="mb-2 flex items-baseline gap-2 px-1">
              <h3 className="font-bold text-ink-900">{day === todayISO() ? 'Bugun' : weekday(day)}</h3>
              <span className="text-sm text-ink-500">{dateLong(day)}</span>
              <span className="ml-auto text-xs font-semibold text-ink-400">{items.length} ta</span>
            </div>
            <div className="card divide-y divide-ink-100 overflow-hidden">
              {items.map((a) => (
                <div key={a.id} className="flex flex-col gap-3 p-4 transition hover:bg-ink-50/60 md:flex-row md:items-center">
                  <button onClick={() => setOpenId(a.id)} className="flex flex-1 items-center gap-4 text-left">
                    <div className="w-14 shrink-0 text-center">
                      <div className="text-base font-extrabold tabular-nums text-ink-900">{time(a.start_at)}</div>
                      <div className="text-xs text-ink-400">{time(a.end_at)}</div>
                    </div>
                    <Avatar name={a.patient.full_name} size={44} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-bold text-ink-900">{a.patient.full_name}</span>
                        <StatusBadge status={a.status} map={APPT_STATUS} />
                      </div>
                      <div className="truncate text-sm text-ink-500">
                        {a.services.map((s) => s.name).join(', ')} · {patientLine(a)}
                      </div>
                      <div className="mt-0.5 flex items-center gap-1 text-xs text-ink-400">
                        {a.place === 'home' ? <Home className="h-3 w-3" /> : <MapPin className="h-3 w-3" />}
                        {a.place === 'home' ? 'Uyga chaqiruv' : a.clinic?.name} · #{a.number}
                      </div>
                    </div>
                  </button>
                  <AppointmentActions a={a} compact />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
      <AppointmentDetailModal id={openId} onClose={() => setOpenId(null)} />
    </div>
  )
}
