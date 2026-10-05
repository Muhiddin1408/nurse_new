import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { ArrowRight, Banknote, CalendarCheck2, Clock, MapPin, Phone, Star, TrendingUp, UserX } from 'lucide-react'
import { addDaysISO, money, phone, time, todayISO } from '@/lib/format'
import { Badge, Button, EmptyState, ErrorState, ListSkeleton, Stat, StatusBadge } from '@/components/ui'
import { useClinic, useClinicProfile } from './ctx'
import type { ClinicAppointment, ClinicReport } from './types'

const CLINIC_STATUS = {
  active: { label: 'Faol', tone: 'green' as const },
  pending: { label: 'Moderatsiyada', tone: 'amber' as const },
  draft: { label: 'Qoralama', tone: 'gray' as const },
  suspended: { label: "To'xtatilgan", tone: 'red' as const },
}

export default function Dashboard() {
  const { capi, clinicId } = useClinic()
  const profile = useClinicProfile()
  const today = todayISO()
  const from = addDaysISO(today, -29)

  const report = useQuery({
    queryKey: ['clinic', clinicId, 'reports', from, today],
    queryFn: () => capi<ClinicReport>('/clinic/reports', { query: { date_from: from, date_to: today } }),
  })
  const appts = useQuery({
    queryKey: ['clinic', clinicId, 'appointments', today, ''],
    queryFn: () => capi<ClinicAppointment[]>('/clinic/appointments', { query: { date: today } }),
  })

  const p = profile.data
  const t = report.data?.total
  const upcoming = (appts.data ?? []).filter((a) => a.status !== 'cancelled')
  const toCollect = upcoming.reduce((s, a) => s + BigInt((a.to_collect || '0').split('.')[0]), 0n)

  return (
    <div className="space-y-6">
      {p && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="relative overflow-hidden rounded-4xl bg-gradient-to-br from-ink-900 via-ink-900 to-brand-900 p-6 text-white shadow-lift sm:p-8"
        >
          <div className="pointer-events-none absolute -right-20 -top-20 h-72 w-72 rounded-full bg-brand-500/30 blur-3xl" />
          <div className="pointer-events-none absolute -bottom-24 left-1/3 h-60 w-60 rounded-full bg-sky-500/20 blur-3xl" />
          <div className="relative flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-4">
              {p.photo_url ? (
                <img src={p.photo_url} alt="" className="h-16 w-16 rounded-2xl object-cover ring-2 ring-white/20" />
              ) : (
                <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-white/10 text-2xl font-extrabold ring-1 ring-white/20">{p.name[0]}</div>
              )}
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h1 className="text-2xl font-extrabold sm:text-3xl">{p.name}</h1>
                  <Badge tone={CLINIC_STATUS[p.status as keyof typeof CLINIC_STATUS]?.tone ?? 'gray'} dot>
                    {CLINIC_STATUS[p.status as keyof typeof CLINIC_STATUS]?.label ?? p.status}
                  </Badge>
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-white/70">
                  <span className="flex items-center gap-1.5"><MapPin className="h-4 w-4" />{p.city}, {p.street}</span>
                  {p.phone && <span className="flex items-center gap-1.5"><Phone className="h-4 w-4" />{phone(p.phone)}</span>}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-3 rounded-2xl bg-white/10 px-4 py-3 ring-1 ring-white/15 backdrop-blur">
              <Star className="h-6 w-6 fill-amber-400 text-amber-400" />
              <div>
                <div className="text-xl font-extrabold">{Number(p.rating) > 0 ? Number(p.rating).toFixed(1) : '—'}</div>
                <div className="text-xs text-white/60">{p.reviews_count} ta sharh</div>
              </div>
            </div>
          </div>
        </motion.div>
      )}

      <div>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-bold text-ink-900">So'nggi 30 kun</h2>
          <Link to="/clinic/reports" className="flex items-center gap-1 text-sm font-semibold text-brand-700 hover:text-brand-800">
            Batafsil hisobot <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
        {report.error ? (
          <div className="card"><ErrorState error={report.error} onRetry={() => report.refetch()} /></div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Stat label="Tushum" value={t ? money(t.revenue) : '…'} icon={<Banknote className="h-5 w-5" />} />
            <Stat label="Bandlik" value={t ? `${t.occupancy_pct}%` : '…'} icon={<TrendingUp className="h-5 w-5" />} tone="sky" hint={t && `${t.slots_booked} / ${t.slots_total} slot`} />
            <Stat label="Yakunlangan qabullar" value={t ? t.completed_or_no_show - t.no_show : '…'} icon={<CalendarCheck2 className="h-5 w-5" />} tone="violet" hint={t && `${t.cancelled} ta bekor qilingan`} />
            <Stat label="Kelmaganlar" value={t ? `${t.no_show_pct}%` : '…'} icon={<UserX className="h-5 w-5" />} tone="rose" hint={t && `${t.no_show} ta qabul`} />
          </div>
        )}
      </div>

      <div className="card overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-ink-100 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-lg font-bold text-ink-900">Bugungi qabullar</h2>
            <p className="text-sm text-ink-500">
              {upcoming.length} ta qabul{toCollect > 0n && <> · kassada olinadi: <b className="text-brand-700">{money(toCollect.toString())}</b></>}
            </p>
          </div>
          <Link to="/clinic/appointments">
            <Button variant="secondary" size="sm">Hammasi <ArrowRight className="h-4 w-4" /></Button>
          </Link>
        </div>
        {appts.isLoading ? (
          <ListSkeleton rows={3} className="p-4" />
        ) : appts.error ? (
          <ErrorState error={appts.error} onRetry={() => appts.refetch()} />
        ) : upcoming.length === 0 ? (
          <EmptyState icon={<Clock className="h-7 w-7" />} title="Bugun qabul yo'q" text="Yangi bronlar shu yerda paydo bo'ladi." />
        ) : (
          <ul className="divide-y divide-ink-100">
            {upcoming.slice(0, 8).map((a) => (
              <li key={a.booking_id} className="flex items-center gap-4 px-5 py-3.5">
                <div className="w-14 shrink-0 text-center">
                  <div className="text-lg font-extrabold tabular-nums text-ink-900">{time(a.start_at)}</div>
                  <div className="text-xs text-ink-400">{time(a.end_at)}</div>
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-semibold text-ink-900">{a.patient}</div>
                  <div className="truncate text-sm text-ink-500">{a.doctor}{a.room && ` · ${a.room}`}</div>
                </div>
                <StatusBadge status={a.status} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
