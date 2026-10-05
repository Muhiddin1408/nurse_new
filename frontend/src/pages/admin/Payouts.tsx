import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Banknote, CheckCircle2 } from 'lucide-react'
import { api } from '@/lib/api'
import { dateShort, money, relativeDateTime, sumMoney } from '@/lib/format'
import { useToast } from '@/lib/toast'
import { Badge, Button, EmptyState, ErrorState, Field, Input, ListSkeleton, Modal, PageHeader, Segmented, Stat } from '@/components/ui'
import type { Payout } from './types'

/** "1 250 000,50" / "1250000.5" → "1250000.50" taqqoslash uchun */
function normalizeAmount(s: string) {
  const clean = s.replace(/[\s']/g, '').replace(',', '.')
  if (!/^\d+(\.\d{0,2})?$/.test(clean)) return null
  const [i, f = ''] = clean.split('.')
  return `${BigInt(i)}.${(f + '00').slice(0, 2)}`
}

export default function Payouts() {
  const [tab, setTab] = useState<'pending' | 'paid'>('pending')
  const [target, setTarget] = useState<Payout | null>(null)
  const q = useQuery({ queryKey: ['mod', 'payouts', tab], queryFn: () => api<Payout[]>('/moderation/payouts', { query: { status: tab } }) })
  const list = q.data ?? []
  const total = list.length ? sumMoney(list.map((p) => p.net_amount)) : '0'

  return (
    <div>
      <PageHeader title="Shifokorlarga to'lovlar" subtitle="Haftalik hisob-kitob davrlari — bank o'tkazmasidan so'ng to'langan deb belgilang" />
      <div className="mb-5 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <Segmented value={tab} onChange={setTab} size="sm" options={[{ value: 'pending', label: 'Kutilmoqda', count: tab === 'pending' ? list.length : undefined }, { value: 'paid', label: "To'langan" }]} />
      </div>
      {list.length > 0 && (
        <div className="mb-5 grid gap-4 sm:grid-cols-2 lg:max-w-2xl">
          <Stat label={tab === 'pending' ? "To'lanishi kerak" : "To'langan jami"} value={money(total)} icon={<Banknote className="h-5 w-5" />} />
          <Stat label="Davrlar soni" value={list.length} tone="sky" icon={<CheckCircle2 className="h-5 w-5" />} />
        </div>
      )}
      {q.isLoading ? (
        <ListSkeleton rows={3} />
      ) : q.error ? (
        <div className="card"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
      ) : list.length === 0 ? (
        <div className="card"><EmptyState icon={<Banknote className="h-7 w-7" />} title={tab === 'pending' ? "To'lanadigan davr yo'q" : "To'lovlar tarixi bo'sh"} text="To'lov davrlari har hafta avtomatik shakllanadi." /></div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {list.map((p) => (
            <div key={p.id} className="card p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-xs font-bold uppercase tracking-wider text-ink-400">Davr</div>
                  <div className="font-bold text-ink-900">{dateShort(p.period_start)} — {dateShort(p.period_end)}</div>
                  <div className="mt-0.5 font-mono text-xs text-ink-400">Shifokor: {p.doctor_id.slice(0, 8)}…</div>
                </div>
                {p.status === 'paid' ? <Badge tone="green" dot>To'langan</Badge> : <Badge tone="amber" dot>Kutilmoqda</Badge>}
              </div>
              <dl className="mt-4 space-y-1.5 text-sm">
                <Row label={`Yalpi (${p.bookings_count} ta qabul)`} value={money(p.gross_amount)} />
                <Row label="Platforma komissiyasi" value={`−${money(p.platform_fee)}`} muted />
                <Row label="To'lov tizimi" value={`−${money(p.provider_fee)}`} muted />
                {Number(p.refunds_amount) > 0 && <Row label="Qaytarilgan" value={`−${money(p.refunds_amount)}`} muted />}
              </dl>
              <div className="mt-3 flex items-center justify-between rounded-2xl bg-brand-50 px-4 py-3">
                <span className="text-sm font-semibold text-brand-800">Shifokorga</span>
                <span className="text-xl font-extrabold tabular-nums text-brand-700">{money(p.net_amount)}</span>
              </div>
              {p.status === 'paid' ? (
                <div className="mt-3 text-xs text-ink-500">
                  {p.paid_at && relativeDateTime(p.paid_at)} · Ref: <span className="font-mono">{p.bank_reference}</span>
                </div>
              ) : (
                <Button block className="mt-4" onClick={() => setTarget(p)}>To'langan deb belgilash</Button>
              )}
            </div>
          ))}
        </div>
      )}
      {target && <MarkPaidModal payout={target} onClose={() => setTarget(null)} />}
    </div>
  )
}

function Row({ label, value, muted }: { label: string; value: string; muted?: boolean }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-ink-500">{label}</dt>
      <dd className={muted ? 'tabular-nums text-ink-500' : 'font-semibold tabular-nums text-ink-900'}>{value}</dd>
    </div>
  )
}

function MarkPaidModal({ payout, onClose }: { payout: Payout; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [ref, setRef] = useState('')
  const [amount, setAmount] = useState('')
  const matches = normalizeAmount(amount) === normalizeAmount(payout.net_amount)

  const m = useMutation({
    mutationFn: () => api<Payout>(`/moderation/payouts/${payout.id}/mark-paid`, { method: 'POST', body: { bank_reference: ref.trim() } }),
    onSuccess: () => {
      toast.success("To'lov belgilandi")
      qc.invalidateQueries({ queryKey: ['mod', 'payouts'] })
      onClose()
    },
    onError: (e) => toast.error(e),
  })

  return (
    <Modal
      open
      onClose={onClose}
      title="To'lovni tasdiqlash"
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Bekor</Button>
          <Button loading={m.isPending} disabled={!ref.trim() || !matches} onClick={() => m.mutate()}>Tasdiqlash</Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="flex items-start gap-3 rounded-2xl bg-amber-50 p-4 text-sm text-amber-800 ring-1 ring-amber-200">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
          <div>Bu amalni <b>qaytarib bo'lmaydi</b>. Faqat bank o'tkazmasi haqiqatan bajarilgandan keyin tasdiqlang.</div>
        </div>
        <div className="rounded-2xl bg-ink-50 px-4 py-3 text-center">
          <div className="text-xs font-semibold text-ink-500">O'tkaziladigan summa</div>
          <div className="text-2xl font-extrabold tabular-nums text-ink-900">{money(payout.net_amount)}</div>
        </div>
        <Field label="Bank to'lov raqami (reference)">
          <Input autoFocus value={ref} onChange={(e) => setRef(e.target.value)} placeholder="Masalan: PP-2026-000123" />
        </Field>
        <Field label="Tasdiqlash uchun summani qayta yozing" error={amount && !matches ? "Summa mos kelmadi" : undefined} hint={matches ? '✓ Summa mos' : undefined}>
          <Input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder={money(payout.net_amount, false)} />
        </Field>
      </div>
    </Modal>
  )
}
