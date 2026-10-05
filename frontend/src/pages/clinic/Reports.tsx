import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Banknote, BarChart3, CalendarCheck2, Download, TrendingUp, UserX } from 'lucide-react'
import { addDaysISO, money, todayISO } from '@/lib/format'
import { Avatar, Button, EmptyState, ErrorState, Field, Input, PageHeader, Segmented, Skeleton, Stat, cx } from '@/components/ui'
import { useClinic } from './ctx'
import { downloadCsv } from './shared'
import type { ClinicReport } from './types'

type Preset = '7' | '30' | '90' | 'custom'

function intOf(v: string) {
  return Number((v || '0').split('.')[0])
}

export default function Reports() {
  const { capi, clinicId } = useClinic()
  const today = todayISO()
  const [preset, setPreset] = useState<Preset>('30')
  const [from, setFrom] = useState(addDaysISO(today, -29))
  const [to, setTo] = useState(today)

  const applyPreset = (p: Preset) => {
    setPreset(p)
    if (p !== 'custom') {
      setFrom(addDaysISO(today, -(Number(p) - 1)))
      setTo(today)
    }
  }

  const q = useQuery({
    queryKey: ['clinic', clinicId, 'reports', from, to],
    queryFn: () => capi<ClinicReport>('/clinic/reports', { query: { date_from: from, date_to: to } }),
    enabled: !!from && !!to && from <= to,
    placeholderData: (prev) => prev,
  })
  const r = q.data
  const doctors = [...(r?.doctors ?? [])].sort((a, b) => intOf(b.revenue) - intOf(a.revenue))
  const maxRev = Math.max(1, ...doctors.map((d) => intOf(d.revenue)))

  const exportCsv = () => {
    if (!r) return
    downloadCsv(`turon-clinic-hisobot-${r.date_from}_${r.date_to}.csv`, [
      ['Shifokor', "Tushum (so'm)", 'Slotlar', 'Band', 'Bandlik %', 'Yakunlangan+kelmagan', 'Kelmagan', 'Kelmagan %', 'Bekor qilingan'],
      ...doctors.map((d) => [d.doctor, d.revenue, d.slots_total, d.slots_booked, d.occupancy_pct, d.completed_or_no_show, d.no_show, d.no_show_pct, d.cancelled]),
      ['JAMI', r.total.revenue, r.total.slots_total, r.total.slots_booked, r.total.occupancy_pct, r.total.completed_or_no_show, r.total.no_show, r.total.no_show_pct, r.total.cancelled],
    ])
  }

  return (
    <div>
      <PageHeader
        title="Hisobotlar"
        subtitle="Tushum, bandlik va kelmaganlar — shifokorlar kesimida"
        actions={<Button variant="outline" icon={<Download className="h-4 w-4" />} disabled={!r} onClick={exportCsv}>CSV yuklab olish</Button>}
      />
      <div className="card mb-5 flex flex-col gap-3 p-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="overflow-x-auto scrollbar-none">
          <Segmented
            value={preset}
            onChange={applyPreset}
            size="sm"
            options={[
              { value: '7', label: '7 kun' },
              { value: '30', label: '30 kun' },
              { value: '90', label: '90 kun' },
              { value: 'custom', label: 'Boshqa' },
            ]}
          />
        </div>
        <div className="flex gap-2">
          <Field label="Dan" className="flex-1">
            <Input type="date" value={from} max={to} onChange={(e) => { setFrom(e.target.value); setPreset('custom') }} className="!rounded-xl !py-2" />
          </Field>
          <Field label="Gacha" className="flex-1">
            <Input type="date" value={to} min={from} max={today} onChange={(e) => { setTo(e.target.value); setPreset('custom') }} className="!rounded-xl !py-2" />
          </Field>
        </div>
      </div>

      {q.isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-28 rounded-3xl" />)}</div>
      ) : q.error ? (
        <div className="card"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
      ) : !r ? null : (
        <div className={cx('space-y-6', q.isFetching && 'opacity-70')}>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Stat label="Tushum" value={money(r.total.revenue)} icon={<Banknote className="h-5 w-5" />} />
            <Stat label="Bandlik" value={`${r.total.occupancy_pct}%`} icon={<TrendingUp className="h-5 w-5" />} tone="sky" hint={`${r.total.slots_booked} / ${r.total.slots_total} slot`} />
            <Stat label="Yakunlangan" value={r.total.completed_or_no_show - r.total.no_show} icon={<CalendarCheck2 className="h-5 w-5" />} tone="violet" hint={`${r.total.cancelled} ta bekor qilingan`} />
            <Stat label="Kelmaganlar" value={`${r.total.no_show_pct}%`} icon={<UserX className="h-5 w-5" />} tone="rose" hint={`${r.total.no_show} ta qabul`} />
          </div>

          {doctors.length === 0 ? (
            <div className="card"><EmptyState icon={<BarChart3 className="h-7 w-7" />} title="Bu davrda ma'lumot yo'q" /></div>
          ) : (
            <>
              <div className="card p-5">
                <h3 className="mb-4 font-bold text-ink-900">Shifokorlar bo'yicha tushum</h3>
                <div className="space-y-3">
                  {doctors.map((d) => {
                    const pct = (intOf(d.revenue) / maxRev) * 100
                    return (
                      <div key={d.doctor_id} className="grid grid-cols-[minmax(0,9rem)_1fr_auto] items-center gap-3 sm:grid-cols-[minmax(0,14rem)_1fr_auto]">
                        <div className="truncate text-sm font-semibold text-ink-700">{d.doctor}</div>
                        <svg viewBox="0 0 100 10" preserveAspectRatio="none" className="h-7 w-full overflow-visible" role="img" aria-label={`${d.doctor}: ${money(d.revenue)}`}>
                          <defs>
                            <linearGradient id="rev-bar" x1="0" x2="1">
                              <stop offset="0" stopColor="#34d3ad" />
                              <stop offset="1" stopColor="#059679" />
                            </linearGradient>
                          </defs>
                          <rect x="0" y="0" width="100" height="10" rx="2" fill="#eceff5" />
                          <rect x="0" y="0" width={Math.max(pct, 0.8)} height="10" rx="2" fill="url(#rev-bar)" />
                        </svg>
                        <div className="w-28 text-right text-sm font-bold tabular-nums text-ink-900">{money(d.revenue)}</div>
                      </div>
                    )
                  })}
                </div>
              </div>

              <div className="card overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[720px] text-left text-sm">
                    <thead className="bg-ink-50/80 text-xs font-bold uppercase tracking-wider text-ink-500">
                      <tr>
                        <th className="px-5 py-3">Shifokor</th>
                        <th className="px-3 py-3 text-right">Tushum</th>
                        <th className="px-3 py-3 text-right">Bandlik</th>
                        <th className="px-3 py-3 text-right">Qabullar</th>
                        <th className="px-3 py-3 text-right">Kelmagan</th>
                        <th className="px-5 py-3 text-right">Bekor</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-ink-100">
                      {doctors.map((d) => (
                        <tr key={d.doctor_id} className="hover:bg-ink-50/60">
                          <td className="px-5 py-3">
                            <div className="flex items-center gap-2.5">
                              <Avatar name={d.doctor} size={32} className="rounded-lg" />
                              <span className="font-semibold text-ink-900">{d.doctor}</span>
                            </div>
                          </td>
                          <td className="px-3 py-3 text-right font-semibold tabular-nums">{money(d.revenue)}</td>
                          <td className="px-3 py-3 text-right tabular-nums">
                            <div className="flex items-center justify-end gap-2">
                              <div className="hidden h-1.5 w-16 overflow-hidden rounded-full bg-ink-100 sm:block">
                                <div className="h-full rounded-full bg-sky-500" style={{ width: `${Math.min(100, d.occupancy_pct)}%` }} />
                              </div>
                              {d.occupancy_pct}%
                            </div>
                            <div className="text-xs text-ink-400">{d.slots_booked}/{d.slots_total}</div>
                          </td>
                          <td className="px-3 py-3 text-right tabular-nums">{d.completed_or_no_show}</td>
                          <td className={cx('px-3 py-3 text-right tabular-nums', d.no_show_pct >= 10 && 'font-bold text-rose-600')}>{d.no_show} <span className="text-xs text-ink-400">({d.no_show_pct}%)</span></td>
                          <td className="px-5 py-3 text-right tabular-nums">{d.cancelled}</td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot className="bg-ink-50/80 font-bold">
                      <tr>
                        <td className="px-5 py-3">Jami</td>
                        <td className="px-3 py-3 text-right tabular-nums">{money(r.total.revenue)}</td>
                        <td className="px-3 py-3 text-right tabular-nums">{r.total.occupancy_pct}%</td>
                        <td className="px-3 py-3 text-right tabular-nums">{r.total.completed_or_no_show}</td>
                        <td className="px-3 py-3 text-right tabular-nums">{r.total.no_show}</td>
                        <td className="px-5 py-3 text-right tabular-nums">{r.total.cancelled}</td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
