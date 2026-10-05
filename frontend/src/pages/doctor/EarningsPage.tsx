import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banknote, CreditCard, Download, FileText, Landmark, PiggyBank, Receipt, TrendingUp, Wallet } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { useToast } from '@/lib/toast'
import { dateLong, dateShort, money, sumMoney } from '@/lib/format'
import { Badge, Button, EmptyState, ErrorState, Field, Input, Modal, PageHeader, Segmented, Skeleton, Stat, cx } from '@/components/ui'
import { SectionCard, type Payout, type Summary, type Transaction } from './shared'

type Period = 'today' | 'week' | 'month'

interface PayoutAccount {
  holder_name: string
  bank_name: string
  masked_number: string
  mfo: string
  inn: string
}

const PAYOUT_STATUS: Record<string, { label: string; tone: 'amber' | 'green' | 'gray' }> = {
  pending: { label: 'Kutilmoqda', tone: 'amber' },
  paid: { label: "To'langan", tone: 'green' },
}

export default function EarningsPage() {
  const [period, setPeriod] = useState<Period>('month')

  const summary = useQuery({
    queryKey: ['doctor', 'earnings', 'summary', period],
    queryFn: () => api<Summary>('/doctor/earnings/summary', { query: { period } }),
  })
  const s = summary.data
  const tx = useQuery({
    queryKey: ['doctor', 'earnings', 'tx', s?.period_from, s?.period_to],
    queryFn: () => api<Summary & { transactions: Transaction[] }>('/doctor/earnings/transactions', { query: { from: s!.period_from, to: s!.period_to } }),
    enabled: !!s,
  })

  const byDay = useMemo(() => {
    const m = new Map<string, string[]>()
    for (const t of tx.data?.transactions ?? []) m.set(t.date, [...(m.get(t.date) ?? []), t.net])
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([d, v]) => ({ d, net: sumMoney(v) }))
  }, [tx.data])
  const max = Math.max(1, ...byDay.map((x) => Number(x.net)))

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Moliya"
        subtitle={s ? `${dateLong(s.period_from)} — ${dateLong(s.period_to)}` : 'Daromad, to‘lovlar va rekvizitlar'}
        actions={
          <Segmented
            size="sm"
            value={period}
            onChange={setPeriod}
            options={[
              { value: 'today', label: 'Bugun' },
              { value: 'week', label: 'Hafta' },
              { value: 'month', label: 'Oy' },
            ]}
          />
        }
      />

      {summary.isError && <ErrorState error={summary.error} onRetry={() => summary.refetch()} />}
      <div className="mb-6 grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        <Stat label="Sof daromad" value={s ? money(s.net) : <Skeleton className="h-8 w-28" />} icon={<Wallet className="h-5 w-5" />} hint={s ? `${s.bookings_count} ta qabul` : undefined} />
        <Stat label="Yalpi tushum" value={s ? money(s.gross) : <Skeleton className="h-8 w-28" />} icon={<TrendingUp className="h-5 w-5" />} tone="sky" />
        <Stat label="To'lanishi kutilmoqda" value={s ? money(s.awaiting_payout) : <Skeleton className="h-8 w-28" />} icon={<PiggyBank className="h-5 w-5" />} tone="amber" />
        <Stat label="To'lab berilgan" value={s ? money(s.paid_out) : <Skeleton className="h-8 w-28" />} icon={<Banknote className="h-5 w-5" />} tone="violet" />
      </div>

      <div className="mb-6 grid gap-6 lg:grid-cols-3">
        <SectionCard title="Kunlik daromad" className="lg:col-span-2">
          {tx.isLoading && <Skeleton className="h-52" />}
          {tx.data && byDay.length === 0 && <EmptyState icon={<TrendingUp className="h-7 w-7" />} title="Bu davrda daromad yo'q" text="Yakunlangan qabullar shu yerda ko'rinadi." />}
          {byDay.length > 0 && (
            <div className="flex h-56 items-end gap-1.5 overflow-x-auto pb-1 sm:gap-2">
              {byDay.map((x) => (
                <div key={x.d} className="group flex min-w-[28px] flex-1 flex-col items-center gap-1.5">
                  <span className="whitespace-nowrap text-[10px] font-bold text-ink-500 opacity-0 transition group-hover:opacity-100">{money(x.net, false)}</span>
                  <div className="w-full rounded-t-xl bg-gradient-to-t from-brand-600 to-brand-400 shadow-glow transition group-hover:from-brand-700" style={{ height: `${(Number(x.net) / max) * 170}px`, minHeight: 6 }} />
                  <span className="text-[10px] font-semibold text-ink-400">{dateShort(x.d)}</span>
                </div>
              ))}
            </div>
          )}
        </SectionCard>
        <SectionCard title="Taqsimot">
          {s ? (
            <div className="space-y-3 text-sm">
              <Row label="Yalpi tushum" value={money(s.gross)} />
              <Row label="Platforma komissiyasi" value={`− ${money(s.platform_fee)}`} muted />
              <Row label="To'lov tizimi xizmati" value={`− ${money(s.provider_fee)}`} muted />
              <Row label="Qaytarilgan" value={`− ${money(s.refunds)}`} muted />
              <div className="h-px bg-ink-100" />
              <Row label="Sof daromad" value={money(s.net)} strong />
            </div>
          ) : (
            <Skeleton className="h-40" />
          )}
        </SectionCard>
      </div>

      <SectionCard title="Tranzaksiyalar" className="mb-6">
        {tx.isLoading && <Skeleton className="h-40" />}
        {tx.isError && <ErrorState error={tx.error} onRetry={() => tx.refetch()} />}
        {tx.data && tx.data.transactions.length === 0 && <p className="py-6 text-center text-sm text-ink-500">Tranzaksiyalar yo'q</p>}
        {tx.data && tx.data.transactions.length > 0 && (
          <div className="-mx-5 overflow-x-auto sm:-mx-6">
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className="border-b border-ink-100 text-left text-xs font-bold uppercase tracking-wide text-ink-400">
                  <th className="px-5 py-2 sm:px-6">Sana</th>
                  <th className="py-2">Bemor</th>
                  <th className="py-2">Xizmat</th>
                  <th className="py-2 text-right">Yalpi</th>
                  <th className="py-2 text-right">Sof</th>
                  <th className="px-5 py-2 text-right sm:px-6">To'lov</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-50">
                {tx.data.transactions.map((t) => (
                  <tr key={t.booking_id} className="hover:bg-ink-50/50">
                    <td className="whitespace-nowrap px-5 py-3 text-ink-600 sm:px-6">{dateShort(t.date)}</td>
                    <td className="py-3 font-semibold text-ink-800">{t.patient}</td>
                    <td className="max-w-[200px] truncate py-3 text-ink-500">{t.services.join(', ')}</td>
                    <td className="py-3 text-right tabular-nums text-ink-600">{money(t.gross)}</td>
                    <td className="py-3 text-right font-bold tabular-nums text-ink-900">{money(t.net)}</td>
                    <td className="px-5 py-3 text-right sm:px-6">
                      {t.payout_status ? <Badge tone={PAYOUT_STATUS[t.payout_status]?.tone ?? 'gray'}>{PAYOUT_STATUS[t.payout_status]?.label ?? t.payout_status}</Badge> : <Badge tone="gray">Navbatda</Badge>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      <div className="grid gap-6 lg:grid-cols-3">
        <Payouts />
        <PayoutAccountCard />
      </div>
    </div>
  )
}

function Row({ label, value, muted, strong }: { label: string; value: string; muted?: boolean; strong?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className={cx(muted ? 'text-ink-500' : 'text-ink-700', strong && 'font-bold text-ink-900')}>{label}</span>
      <span className={cx('tabular-nums', strong ? 'text-lg font-extrabold text-brand-700' : muted ? 'text-ink-500' : 'font-semibold text-ink-800')}>{value}</span>
    </div>
  )
}

function Payouts() {
  const toast = useToast()
  const q = useQuery({ queryKey: ['doctor', 'payouts'], queryFn: () => api<Payout[]>('/doctor/payouts') })
  const [loading, setLoading] = useState<string | null>(null)

  const openStatement = async (p: Payout) => {
    setLoading(p.id)
    try {
      const data = await api<unknown>(`/doctor/payouts/${p.id}/statement`)
      const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2)
      const blob = new Blob([text], { type: typeof data === 'string' && text.includes(',') ? 'text/csv;charset=utf-8' : 'text/plain;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `hisobot-${p.period_start}_${p.period_end}.${blob.type.startsWith('text/csv') ? 'csv' : 'txt'}`
      a.click()
      setTimeout(() => URL.revokeObjectURL(url), 2000)
    } catch (e) {
      toast.error(e)
    } finally {
      setLoading(null)
    }
  }

  return (
    <SectionCard title="To'lovlar (payout)" subtitle="Platforma hisobingizga o'tkazgan summalar" className="lg:col-span-2">
      {q.isLoading && <Skeleton className="h-32" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && q.data.length === 0 && <EmptyState icon={<Receipt className="h-7 w-7" />} title="Hali to'lovlar yo'q" text="Har davr oxirida yakunlangan qabullar bo'yicha to'lov hisoblanadi." />}
      <div className="space-y-2">
        {q.data?.map((p) => (
          <div key={p.id} className="flex flex-col gap-3 rounded-2xl border border-ink-100 p-4 sm:flex-row sm:items-center">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-brand-50 text-brand-600">
              <FileText className="h-5 w-5" />
            </div>
            <div className="flex-1">
              <div className="font-bold text-ink-900">
                {dateShort(p.period_start)} — {dateShort(p.period_end)}
              </div>
              <div className="text-sm text-ink-500">
                {p.bookings_count} ta qabul · yalpi {money(p.gross_amount)}
                {p.paid_at && ` · ${dateLong(p.paid_at)} to'langan`}
              </div>
            </div>
            <div className="flex items-center gap-3">
              <div className="text-right">
                <div className="font-extrabold text-ink-900">{money(p.net_amount)}</div>
                <Badge tone={PAYOUT_STATUS[p.status]?.tone ?? 'gray'}>{PAYOUT_STATUS[p.status]?.label ?? p.status}</Badge>
              </div>
              <Button size="sm" variant="outline" icon={<Download className="h-4 w-4" />} loading={loading === p.id} onClick={() => openStatement(p)}>
                Hisobot
              </Button>
            </div>
          </div>
        ))}
      </div>
    </SectionCard>
  )
}

function PayoutAccountCard() {
  const qc = useQueryClient()
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ holder_name: '', bank_name: '', account_number: '', mfo: '', inn: '' })
  const [errors, setErrors] = useState<Record<string, string>>({})

  const q = useQuery({
    queryKey: ['doctor', 'payout-account'],
    queryFn: async () => {
      try {
        return await api<PayoutAccount>('/doctor/payout-account')
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null
        throw e
      }
    },
  })

  const save = useMutation({
    mutationFn: () => api<PayoutAccount>('/doctor/payout-account', { method: 'PUT', body: { ...form, account_number: form.account_number.replace(/\s/g, '') } }),
    onSuccess: () => {
      toast.success('Rekvizitlar saqlandi')
      setOpen(false)
      qc.invalidateQueries({ queryKey: ['doctor', 'payout-account'] })
    },
    onError: (e) => {
      if (e instanceof ApiError) setErrors(e.fields)
      toast.error(e)
    },
  })

  const acc = q.data
  return (
    <SectionCard title="To'lov rekvizitlari">
      {q.isLoading && <Skeleton className="h-40" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data === null && (
        <EmptyState
          className="py-6"
          icon={<Landmark className="h-7 w-7" />}
          title="Rekvizit kiritilmagan"
          text="To'lovlarni olish uchun bank hisobingizni kiriting."
          action={
            <Button size="sm" onClick={() => setOpen(true)}>
              Kiritish
            </Button>
          }
        />
      )}
      {acc && (
        <div>
          <div className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-ink-900 via-ink-800 to-brand-900 p-5 text-white shadow-lift">
            <div className="absolute -right-10 -top-10 h-32 w-32 rounded-full bg-brand-400/20 blur-2xl" />
            <CreditCard className="h-7 w-7 text-brand-300" />
            <div className="mt-6 font-mono text-lg tracking-widest">{acc.masked_number}</div>
            <div className="mt-3 flex justify-between text-xs text-white/70">
              <span>{acc.holder_name}</span>
              <span>{acc.bank_name}</span>
            </div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
            <div className="rounded-xl bg-ink-50 p-2.5">
              <div className="text-xs text-ink-400">MFO</div>
              <div className="font-semibold">{acc.mfo || '—'}</div>
            </div>
            <div className="rounded-xl bg-ink-50 p-2.5">
              <div className="text-xs text-ink-400">INN</div>
              <div className="font-semibold">{acc.inn || '—'}</div>
            </div>
          </div>
          <Button
            block
            variant="outline"
            size="sm"
            className="mt-3"
            onClick={() => {
              setForm({ holder_name: acc.holder_name, bank_name: acc.bank_name, account_number: '', mfo: acc.mfo, inn: acc.inn })
              setOpen(true)
            }}
          >
            O'zgartirish
          </Button>
        </div>
      )}

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Bank rekvizitlari"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Bekor
            </Button>
            <Button loading={save.isPending} disabled={!form.holder_name || !form.account_number} onClick={() => save.mutate()}>
              Saqlash
            </Button>
          </>
        }
      >
        <div className="mb-4 rounded-2xl bg-amber-50 p-3 text-sm text-amber-800">Bu sezgir ma'lumot. Hisob raqamini diqqat bilan tekshiring — to'lovlar shu hisobga o'tkaziladi.</div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Hisob egasi *" error={errors.holder_name} className="sm:col-span-2">
            <Input value={form.holder_name} onChange={(e) => setForm({ ...form, holder_name: e.target.value })} placeholder="Familiya Ism" />
          </Field>
          <Field label="Hisob raqami (20 xona) *" error={errors.account_number} className="sm:col-span-2">
            <Input inputMode="numeric" value={form.account_number} onChange={(e) => setForm({ ...form, account_number: e.target.value.replace(/[^\d\s]/g, '') })} placeholder="2020 8000 ..." />
          </Field>
          <Field label="Bank" error={errors.bank_name} className="sm:col-span-2">
            <Input value={form.bank_name} onChange={(e) => setForm({ ...form, bank_name: e.target.value })} placeholder="Kapitalbank" />
          </Field>
          <Field label="MFO" error={errors.mfo}>
            <Input value={form.mfo} onChange={(e) => setForm({ ...form, mfo: e.target.value })} />
          </Field>
          <Field label="INN" error={errors.inn}>
            <Input value={form.inn} onChange={(e) => setForm({ ...form, inn: e.target.value })} />
          </Field>
        </div>
      </Modal>
    </SectionCard>
  )
}
