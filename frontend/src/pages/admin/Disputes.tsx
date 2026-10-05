import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock, Scale, Stethoscope, User } from 'lucide-react'
import { api } from '@/lib/api'
import { money, relativeDateTime } from '@/lib/format'
import { useToast } from '@/lib/toast'
import type { Dispute } from '@/lib/types'
import { Button, EmptyState, ErrorState, Field, Input, ListSkeleton, Modal, PageHeader, Segmented, StatusBadge, Textarea, cx } from '@/components/ui'
import { DISPUTE_REASON, DISPUTE_STATUS } from './types'

export default function Disputes() {
  const [tab, setTab] = useState<'open' | 'resolved'>('open')
  const [target, setTarget] = useState<Dispute | null>(null)
  const q = useQuery({ queryKey: ['mod', 'disputes'], queryFn: () => api<Dispute[]>('/booking/disputes') })

  const all = q.data ?? []
  const list = all
    .filter((d) => (tab === 'open' ? d.status === 'open' : d.status !== 'open'))
    .sort((a, b) => (tab === 'open' ? a.due_at.localeCompare(b.due_at) : b.created_at.localeCompare(a.created_at)))
  const openCount = all.filter((d) => d.status === 'open').length

  return (
    <div className="max-w-4xl">
      <PageHeader title="Nizolar" subtitle="Mijozlar shikoyatlari — muddat bo'yicha saralangan" />
      <div className="mb-5">
        <Segmented value={tab} onChange={setTab} size="sm" options={[{ value: 'open', label: 'Ochiq', count: openCount }, { value: 'resolved', label: 'Hal qilingan' }]} />
      </div>
      {q.isLoading ? (
        <ListSkeleton rows={3} />
      ) : q.error ? (
        <div className="card"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
      ) : list.length === 0 ? (
        <div className="card"><EmptyState icon={<Scale className="h-7 w-7" />} title={tab === 'open' ? "Ochiq nizo yo'q" : "Hal qilingan nizolar yo'q"} /></div>
      ) : (
        <div className="space-y-3">
          {list.map((d) => {
            const overdue = d.status === 'open' && new Date(d.due_at).getTime() < Date.now()
            return (
              <div key={d.id} className={cx('card p-5', overdue && 'ring-rose-200')}>
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-bold text-ink-900">{DISPUTE_REASON[d.reason] ?? d.reason}</span>
                      <StatusBadge status={d.status} map={DISPUTE_STATUS} />
                    </div>
                    <div className="mt-1 font-mono text-xs text-ink-400">Bron: {d.booking_id.slice(0, 8)}… · {relativeDateTime(d.created_at)}</div>
                  </div>
                  {d.status === 'open' && (
                    <span className={cx('chip shrink-0', overdue ? 'bg-rose-50 text-rose-700 ring-1 ring-rose-200' : 'bg-amber-50 text-amber-700')}>
                      <Clock className="h-3.5 w-3.5" />
                      {overdue ? 'Muddat o‘tgan: ' : 'Muddat: '}
                      {relativeDateTime(d.due_at)}
                    </span>
                  )}
                </div>
                {d.description && <p className="mt-3 whitespace-pre-line rounded-2xl bg-ink-50 p-4 text-[15px] leading-relaxed text-ink-700">{d.description}</p>}
                {d.status !== 'open' && d.resolution_note && (
                  <div className="mt-3 rounded-2xl bg-brand-50/60 p-4 text-sm text-ink-700 ring-1 ring-brand-100">
                    <b className="text-brand-800">Qaror:</b> {d.resolution_note}
                    {d.refund_id && <span className="ml-1 text-brand-700">· pul qaytarildi</span>}
                  </div>
                )}
                {d.status === 'open' && (
                  <div className="mt-4 flex justify-end">
                    <Button size="sm" onClick={() => setTarget(d)}>Hal qilish</Button>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
      {target && <ResolveModal dispute={target} onClose={() => setTarget(null)} />}
    </div>
  )
}

function ResolveModal({ dispute, onClose }: { dispute: Dispute; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [side, setSide] = useState<'client' | 'doctor'>('client')
  const [note, setNote] = useState('')
  const [refund, setRefund] = useState('')
  const refundClean = refund.replace(/\s/g, '').replace(',', '.')
  const refundValid = !refundClean || /^\d+(\.\d{1,2})?$/.test(refundClean)

  const m = useMutation({
    mutationFn: () =>
      api<Dispute>(`/booking/disputes/${dispute.id}/resolve`, {
        method: 'POST',
        body: { in_favor_of: side, note: note.trim(), ...(side === 'client' && refundClean ? { refund_amount: refundClean } : {}) },
      }),
    onSuccess: () => {
      toast.success('Nizo hal qilindi')
      qc.invalidateQueries({ queryKey: ['mod', 'disputes'] })
      onClose()
    },
    onError: (e) => toast.error(e),
  })

  return (
    <Modal
      open
      onClose={onClose}
      title="Nizoni hal qilish"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Bekor</Button>
          <Button loading={m.isPending} disabled={!note.trim() || !refundValid} onClick={() => m.mutate()}>Qarorni saqlash</Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="rounded-2xl bg-ink-50 p-3 text-sm text-ink-600">
          <b className="text-ink-900">{DISPUTE_REASON[dispute.reason] ?? dispute.reason}</b>
          {dispute.description && <p className="mt-1 line-clamp-3">{dispute.description}</p>}
        </div>
        <div>
          <span className="label">Kimning foydasiga</span>
          <div className="grid grid-cols-2 gap-3">
            {([
              { v: 'client', label: 'Mijoz', icon: User },
              { v: 'doctor', label: 'Shifokor', icon: Stethoscope },
            ] as const).map((o) => (
              <button
                key={o.v}
                type="button"
                onClick={() => setSide(o.v)}
                className={cx('flex items-center justify-center gap-2 rounded-2xl border-2 px-4 py-3 font-semibold transition', side === o.v ? 'border-brand-500 bg-brand-50 text-brand-800' : 'border-ink-200 text-ink-600 hover:border-ink-300')}
              >
                <o.icon className="h-4 w-4" />
                {o.label}
              </button>
            ))}
          </div>
        </div>
        {side === 'client' && (
          <Field label="Qaytariladigan summa (so'm)" error={!refundValid ? "Noto'g'ri summa" : undefined} hint={refundClean && refundValid ? `${money(refundClean)} mijozga qaytariladi. Bron summasidan oshmasligi kerak.` : "Bo'sh qoldirilsa pul qaytarilmaydi"}>
            <Input inputMode="decimal" value={refund} onChange={(e) => setRefund(e.target.value.replace(/[^\d.,\s]/g, ''))} placeholder="0" />
          </Field>
        )}
        <Field label="Qaror izohi (majburiy)" hint="Mijoz va shifokorga ko'rsatiladi">
          <Textarea rows={4} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Tekshiruv natijasi va qaror sababi" />
        </Field>
      </div>
    </Modal>
  )
}
