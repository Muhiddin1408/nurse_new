import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Banknote, DoorOpen, Phone, Search, Stethoscope, X } from 'lucide-react'
import { money, phone, time, todayISO } from '@/lib/format'
import { EmptyState, ErrorState, ListSkeleton, PageHeader, StatusBadge, cx } from '@/components/ui'
import { useClinic } from './ctx'
import { DateNav, PAYMENT_MODE, useDebounced } from './shared'
import type { ClinicAppointment } from './types'

export default function Appointments() {
  const { capi, clinicId } = useClinic()
  const [date, setDate] = useState(todayISO())
  const [q, setQ] = useState('')
  const dq = useDebounced(q.trim(), 300)

  const query = useQuery({
    queryKey: ['clinic', clinicId, 'appointments', date, dq],
    queryFn: () => capi<ClinicAppointment[]>('/clinic/appointments', { query: { date, q: dq } }),
    placeholderData: (prev) => prev,
  })
  const rows = query.data ?? []
  const totalCollect = rows.reduce((s, a) => s + BigInt((a.to_collect || '0').split('.')[0]), 0n)

  return (
    <div>
      <PageHeader title="Qabullar" subtitle="Reception: kunlik ro'yxat, qidiruv va kassada olinadigan summa" />

      <div className="card mb-5 flex flex-col gap-3 p-3 lg:flex-row lg:items-center lg:justify-between">
        <DateNav value={date} onChange={setDate} className="flex-wrap" />
        <div className="relative w-full lg:max-w-sm">
          <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Telefon, bemor ismi yoki bron raqami"
            className="field !rounded-xl !py-2.5 pl-10 pr-9"
          />
          {q && (
            <button onClick={() => setQ('')} className="absolute right-3 top-1/2 -translate-y-1/2 rounded-lg p-1 text-ink-400 hover:bg-ink-100">
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>

      {rows.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-3 text-sm">
          <span className="chip bg-white text-ink-700 ring-1 ring-ink-100">Jami: {rows.length}</span>
          {totalCollect > 0n && (
            <span className="chip bg-brand-50 text-brand-800 ring-1 ring-brand-100">
              <Banknote className="h-3.5 w-3.5" /> Kassada olinadi: {money(totalCollect.toString())}
            </span>
          )}
        </div>
      )}

      {query.isLoading ? (
        <ListSkeleton rows={5} />
      ) : query.error ? (
        <div className="card"><ErrorState error={query.error} onRetry={() => query.refetch()} /></div>
      ) : rows.length === 0 ? (
        <div className="card">
          <EmptyState
            icon={<Search className="h-7 w-7" />}
            title={dq ? 'Hech narsa topilmadi' : "Bu kunda qabul yo'q"}
            text={dq ? "Qidiruv so'zini o'zgartirib ko'ring yoki boshqa kunni tanlang." : 'Boshqa kunni tanlab ko‘ring.'}
          />
        </div>
      ) : (
        <>
          {/* Desktop: jadval */}
          <div className={cx('card hidden overflow-hidden md:block', query.isFetching && 'opacity-70')}>
            <table className="w-full text-left text-sm">
              <thead className="bg-ink-50/80 text-xs font-bold uppercase tracking-wider text-ink-500">
                <tr>
                  <th className="px-5 py-3">Vaqt</th>
                  <th className="px-3 py-3">Bemor</th>
                  <th className="px-3 py-3">Shifokor / xona</th>
                  <th className="px-3 py-3">Holat</th>
                  <th className="px-3 py-3">To'lov</th>
                  <th className="px-5 py-3 text-right">Kassada</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-100">
                {rows.map((a) => (
                  <tr key={a.booking_id} className="transition hover:bg-ink-50/60">
                    <td className="px-5 py-3.5">
                      <div className="font-bold tabular-nums text-ink-900">{time(a.start_at)}–{time(a.end_at)}</div>
                      <div className="text-xs text-ink-400">{a.number}</div>
                    </td>
                    <td className="px-3 py-3.5">
                      <div className="font-semibold text-ink-900">{a.patient}</div>
                      {a.client_phone && (
                        <a href={`tel:${a.client_phone}`} className="text-xs text-ink-500 hover:text-brand-700">{phone(a.client_phone)}</a>
                      )}
                    </td>
                    <td className="px-3 py-3.5">
                      <div className="text-ink-800">{a.doctor}</div>
                      <div className="text-xs text-ink-400">{a.room ?? 'Xona biriktirilmagan'}</div>
                    </td>
                    <td className="px-3 py-3.5"><StatusBadge status={a.status} /></td>
                    <td className="px-3 py-3.5">
                      <div className="text-ink-700">{PAYMENT_MODE[a.payment_mode] ?? a.payment_mode}</div>
                      <div className="text-xs text-ink-400">{money(a.total_price)}</div>
                    </td>
                    <td className="px-5 py-3.5 text-right">
                      {Number(a.to_collect) > 0 ? (
                        <span className="inline-flex rounded-xl bg-brand-50 px-3 py-1.5 font-extrabold tabular-nums text-brand-700 ring-1 ring-brand-100">{money(a.to_collect)}</span>
                      ) : (
                        <span className="text-ink-300">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobil: kartalar */}
          <div className="space-y-3 md:hidden">
            {rows.map((a) => (
              <div key={a.booking_id} className="card p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-lg font-extrabold tabular-nums text-ink-900">{time(a.start_at)}–{time(a.end_at)}</div>
                    <div className="text-xs text-ink-400">{a.number}</div>
                  </div>
                  <StatusBadge status={a.status} />
                </div>
                <div className="mt-3 font-semibold text-ink-900">{a.patient}</div>
                <div className="mt-2 space-y-1.5 text-sm text-ink-600">
                  <div className="flex items-center gap-2"><Stethoscope className="h-4 w-4 text-ink-400" />{a.doctor}</div>
                  {a.room && <div className="flex items-center gap-2"><DoorOpen className="h-4 w-4 text-ink-400" />{a.room}</div>}
                  {a.client_phone && (
                    <a href={`tel:${a.client_phone}`} className="flex items-center gap-2 text-brand-700"><Phone className="h-4 w-4" />{phone(a.client_phone)}</a>
                  )}
                </div>
                <div className="mt-3 flex items-center justify-between rounded-2xl bg-ink-50 px-3 py-2.5 text-sm">
                  <span className="text-ink-500">{PAYMENT_MODE[a.payment_mode] ?? a.payment_mode}</span>
                  {Number(a.to_collect) > 0 ? (
                    <span className="font-extrabold text-brand-700">Kassada: {money(a.to_collect)}</span>
                  ) : (
                    <span className="font-semibold text-ink-700">{money(a.total_price)}</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
