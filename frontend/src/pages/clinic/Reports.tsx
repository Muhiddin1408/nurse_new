import { useState } from 'react'
import { Banknote, BarChart3, CalendarCheck2, Download, Undo2, UserX } from 'lucide-react'
import { addDaysISO, money, todayISO } from '@/lib/format'
import { Avatar, Button, EmptyState, ErrorState, Field, Input, PageHeader, Segmented, Skeleton, Stat, cx } from '@/components/ui'
import { useClinicReport } from './ctx'
import { downloadCsv } from './shared'

type Preset = '7' | '30' | '90' | 'custom'

const pct = (part: number, whole: number) => (whole ? Math.round((part / whole) * 100) : 0)

export default function Reports() {
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

  // billing: onlayn to'langan bronlar (klinika kassasidagi to'lovlar bu hisobotga kirmaydi)
  const q = useClinicReport(from, to)
  const r = q.data
  const doctors = [...(r?.doctors ?? [])].sort((a, b) => b.revenue_tiyin - a.revenue_tiyin)
  const maxRev = Math.max(1, ...doctors.map((d) => d.revenue_tiyin))
  const som = (t: number) => (t / 100).toFixed(2)

  const exportCsv = () => {
    if (!r) return
    downloadCsv(`turon-clinic-hisobot-${r.date_from}_${r.date_to}.csv`, [
      ['Shifokor', "Onlayn tushum (so'm)", "Qaytarilgan (so'm)", 'Bronlar', 'Yakunlangan', 'Kelmagan', 'Bekor qilingan'],
      ...doctors.map((d) => [d.doctor, som(d.revenue_tiyin), som(d.refunds_tiyin), d.bookings, d.completed, d.no_show, d.cancelled]),
      ['JAMI', som(r.total.revenue_tiyin), som(r.total.refunds_tiyin), r.total.bookings, r.total.completed, r.total.no_show, r.total.cancelled],
    ])
  }

  return (
    <div>
      <PageHeader
        title="Hisobotlar"
        subtitle="Onlayn to'langan bronlar: tushum, qaytarishlar va kelmaganlar — shifokorlar kesimida"
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
            <Stat label="Onlayn tushum" value={money(r.total.revenue_tiyin)} icon={<Banknote className="h-5 w-5" />} hint={`${r.total.bookings} ta to'langan bron`} />
            <Stat label="Qaytarilgan" value={money(r.total.refunds_tiyin)} icon={<Undo2 className="h-5 w-5" />} tone="sky" />
            <Stat label="Yakunlangan" value={r.total.completed} icon={<CalendarCheck2 className="h-5 w-5" />} tone="violet" hint={`${r.total.cancelled} ta bekor qilingan`} />
            <Stat label="Kelmaganlar" value={`${pct(r.total.no_show, r.total.bookings)}%`} icon={<UserX className="h-5 w-5" />} tone="rose" hint={`${r.total.no_show} ta qabul`} />
          </div>

          {doctors.length === 0 ? (
            <div className="card"><EmptyState icon={<BarChart3 className="h-7 w-7" />} title="Bu davrda ma'lumot yo'q" /></div>
          ) : (
            <>
              <div className="card p-5">
                <h3 className="mb-4 font-bold text-ink-900">Shifokorlar bo'yicha tushum</h3>
                <div className="space-y-3">
                  {doctors.map((d) => {
                    const share = (d.revenue_tiyin / maxRev) * 100
                    return (
                      <div key={d.doctor_id} className="grid grid-cols-[minmax(0,9rem)_1fr_auto] items-center gap-3 sm:grid-cols-[minmax(0,14rem)_1fr_auto]">
                        <div className="truncate text-sm font-semibold text-ink-700">{d.doctor}</div>
                        <svg viewBox="0 0 100 10" preserveAspectRatio="none" className="h-7 w-full overflow-visible" role="img" aria-label={`${d.doctor}: ${money(d.revenue_tiyin)}`}>
                          <defs>
                            <linearGradient id="rev-bar" x1="0" x2="1">
                              <stop offset="0" stopColor="#34d3ad" />
                              <stop offset="1" stopColor="#059679" />
                            </linearGradient>
                          </defs>
                          <rect x="0" y="0" width="100" height="10" rx="2" fill="#eceff5" />
                          <rect x="0" y="0" width={Math.max(share, 0.8)} height="10" rx="2" fill="url(#rev-bar)" />
                        </svg>
                        <div className="w-28 text-right text-sm font-bold tabular-nums text-ink-900">{money(d.revenue_tiyin)}</div>
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
                        <th className="px-3 py-3 text-right">Qaytarilgan</th>
                        <th className="px-3 py-3 text-right">Yakunlangan</th>
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
                          <td className="px-3 py-3 text-right font-semibold tabular-nums">{money(d.revenue_tiyin)}</td>
                          <td className="px-3 py-3 text-right tabular-nums">{money(d.refunds_tiyin)}</td>
                          <td className="px-3 py-3 text-right tabular-nums">{d.completed}</td>
                          <td className={cx('px-3 py-3 text-right tabular-nums', pct(d.no_show, d.bookings) >= 10 && 'font-bold text-rose-600')}>{d.no_show} <span className="text-xs text-ink-400">({pct(d.no_show, d.bookings)}%)</span></td>
                          <td className="px-5 py-3 text-right tabular-nums">{d.cancelled}</td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot className="bg-ink-50/80 font-bold">
                      <tr>
                        <td className="px-5 py-3">Jami</td>
                        <td className="px-3 py-3 text-right tabular-nums">{money(r.total.revenue_tiyin)}</td>
                        <td className="px-3 py-3 text-right tabular-nums">{money(r.total.refunds_tiyin)}</td>
                        <td className="px-3 py-3 text-right tabular-nums">{r.total.completed}</td>
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
