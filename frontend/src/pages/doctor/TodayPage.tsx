import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { CalendarClock, CalendarDays, CheckCircle2, Coffee, Home, MapPin, Timer, Wallet } from 'lucide-react'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { dateLong, money, time, todayISO, weekday } from '@/lib/format'
import { Avatar, Button, EmptyState, ErrorState, ListSkeleton, Stat, StatusBadge, cx } from '@/components/ui'
import { AppointmentActions, AppointmentDetailModal, addressLine, patientLine } from './appointments-ui'
import { APPT_STATUS, type Appointment, type Summary } from './shared'

function useNow(ms = 30_000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(t)
  }, [ms])
  return now
}

function countdown(ms: number) {
  const m = Math.max(0, Math.round(ms / 60000))
  if (m < 1) return 'hozir'
  if (m < 60) return `${m} daqiqadan keyin`
  const h = Math.floor(m / 60)
  return `${h} soat ${m % 60 ? `${m % 60} daq` : ''} keyin`
}

function greeting() {
  const h = new Date().getHours()
  if (h < 11) return 'Xayrli tong'
  if (h < 17) return 'Xayrli kun'
  return 'Xayrli kech'
}

export default function TodayPage() {
  const { user } = useAuth()
  const now = useNow()
  const [openId, setOpenId] = useState<string | null>(null)

  const q = useQuery({
    queryKey: ['doctor', 'appointments', 'today'],
    queryFn: () => api<Appointment[]>('/doctor/appointments/today'),
    refetchInterval: 60_000,
  })
  const earn = useQuery({
    queryKey: ['doctor', 'earnings', 'summary', 'today'],
    queryFn: () => api<Summary>('/doctor/earnings/summary', { query: { period: 'today' } }),
  })

  const list = useMemo(() => [...(q.data ?? [])].sort((a, b) => a.start_at.localeCompare(b.start_at)), [q.data])
  const current = list.find((a) => a.status === 'confirmed' && (a.started_at || (Date.parse(a.start_at) <= now && Date.parse(a.end_at) > now)))
  const next = list.find((a) => a.status === 'confirmed' && a !== current && Date.parse(a.start_at) > now)
  const done = list.filter((a) => a.status === 'completed').length
  const left = list.filter((a) => a.status === 'confirmed').length

  const name = (user?.full_name || '').replace(/^Dr\.?\s*/i, '').split(' ')[0]

  return (
    <div className="mx-auto max-w-6xl">
      <div className="relative mb-6 overflow-hidden rounded-4xl bg-gradient-to-br from-brand-600 via-brand-700 to-ink-900 p-6 text-white shadow-lift sm:p-8">
        <div className="pointer-events-none absolute -right-20 -top-20 h-72 w-72 rounded-full bg-white/10 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-24 left-1/3 h-64 w-64 rounded-full bg-sky-400/20 blur-3xl" />
        <div className="relative flex flex-col gap-6 md:flex-row md:items-end md:justify-between">
          <div>
            <div className="text-sm font-semibold text-white/70">
              {weekday(todayISO())}, {dateLong(todayISO())}
            </div>
            <h1 className="mt-1 text-2xl font-extrabold sm:text-3xl">
              {greeting()}, {name || 'doktor'}! 👋
            </h1>
            <p className="mt-2 max-w-md text-white/80">
              {list.length === 0 ? "Bugun qabullar yo'q. Jadvalni tekshirib qo'ying." : left > 0 ? `Bugun yana ${left} ta qabul kutmoqda.` : 'Bugungi barcha qabullar yakunlandi. Ajoyib ish!'}
            </p>
          </div>
          {next && (
            <motion.button
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              onClick={() => setOpenId(next.id)}
              className="rounded-3xl bg-white/10 p-4 text-left ring-1 ring-white/20 backdrop-blur transition hover:bg-white/15 md:min-w-[280px]"
            >
              <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-brand-200">
                <Timer className="h-4 w-4" /> Keyingi qabul — {countdown(Date.parse(next.start_at) - now)}
              </div>
              <div className="mt-2 text-lg font-bold">{next.patient.full_name}</div>
              <div className="text-sm text-white/70">
                {time(next.start_at)} · {next.services.map((s) => s.name).join(', ')}
              </div>
            </motion.button>
          )}
        </div>
      </div>

      <div className="mb-6 grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        <Stat label="Bugungi qabullar" value={q.data ? list.filter((a) => a.status !== 'cancelled').length : '—'} icon={<CalendarDays className="h-5 w-5" />} />
        <Stat label="Yakunlangan" value={q.data ? done : '—'} icon={<CheckCircle2 className="h-5 w-5" />} tone="sky" />
        <Stat label="Kutilmoqda" value={q.data ? left : '—'} icon={<CalendarClock className="h-5 w-5" />} tone="amber" />
        <Stat label="Bugungi daromad" value={earn.data ? money(earn.data.net) : '—'} icon={<Wallet className="h-5 w-5" />} tone="violet" hint={earn.data ? `${earn.data.bookings_count} ta qabuldan` : undefined} />
      </div>

      <div className="card p-4 sm:p-6">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-bold">Bugungi kun tartibi</h2>
          <Link to="schedule" className="text-sm font-semibold text-brand-700 hover:underline">
            Jadvalni ochish →
          </Link>
        </div>
        {q.isLoading && <ListSkeleton rows={3} />}
        {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
        {q.data && list.length === 0 && (
          <EmptyState
            icon={<Coffee className="h-7 w-7" />}
            title="Bugun bo'sh kun"
            text="Bugunga hech kim yozilmagan. Bo'sh slotlar ochiq ekanini jadvalda tekshiring."
            action={
              <Link to="schedule">
                <Button variant="secondary">Jadvalga o'tish</Button>
              </Link>
            }
          />
        )}
        {list.length > 0 && (
          <ol className="relative space-y-3">
            <div className="absolute bottom-4 left-[52px] top-4 hidden w-0.5 bg-ink-100 sm:block" />
            {list.map((a, i) => {
              const isCurrent = a === current
              const isNext = a === next
              const past = a.status !== 'confirmed'
              return (
                <motion.li
                  key={a.id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.04 }}
                  className="relative flex gap-3 sm:gap-5"
                >
                  <div className="hidden w-[44px] shrink-0 pt-4 text-right sm:block">
                    <div className={cx('text-sm font-bold tabular-nums', past ? 'text-ink-400' : 'text-ink-900')}>{time(a.start_at)}</div>
                    <div className="text-xs text-ink-400">{time(a.end_at)}</div>
                  </div>
                  <span
                    className={cx(
                      'absolute left-[47px] top-5 z-10 hidden h-3 w-3 rounded-full ring-4 ring-white sm:block',
                      isCurrent ? 'animate-pulse bg-brand-500' : isNext ? 'bg-sky-500' : past ? 'bg-ink-300' : 'bg-ink-200',
                    )}
                  />
                  <div
                    className={cx(
                      'flex-1 rounded-3xl border p-4 transition sm:ml-4',
                      isCurrent ? 'border-brand-200 bg-gradient-to-br from-brand-50 to-white shadow-glow' : isNext ? 'border-sky-200 bg-sky-50/40' : 'border-ink-100 bg-white',
                      past && 'opacity-70',
                    )}
                  >
                    <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                      <button onClick={() => setOpenId(a.id)} className="flex min-w-0 items-center gap-3 text-left">
                        <Avatar name={a.patient.full_name} size={48} />
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-bold text-ink-900">{a.patient.full_name}</span>
                            {isCurrent && <span className="chip bg-brand-500 text-white">Hozir</span>}
                            {isNext && <span className="chip bg-sky-500 text-white">Keyingi</span>}
                            {past && <StatusBadge status={a.status} map={APPT_STATUS} />}
                          </div>
                          <div className="text-sm text-ink-500">
                            <span className="sm:hidden">{time(a.start_at)} · </span>
                            {patientLine(a)}
                          </div>
                          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-600">
                            <span>{a.services.map((s) => s.name).join(', ')}</span>
                            <span className="flex items-center gap-1 text-ink-500">
                              {a.place === 'home' ? <Home className="h-3.5 w-3.5 text-violet-500" /> : <MapPin className="h-3.5 w-3.5 text-sky-500" />}
                              {a.place === 'home' ? addressLine(a) || 'Uyga chaqiruv' : a.clinic?.name}
                            </span>
                          </div>
                        </div>
                      </button>
                      <AppointmentActions a={a} compact />
                    </div>
                  </div>
                </motion.li>
              )
            })}
          </ol>
        )}
      </div>
      <AppointmentDetailModal id={openId} onClose={() => setOpenId(null)} />
    </div>
  )
}
