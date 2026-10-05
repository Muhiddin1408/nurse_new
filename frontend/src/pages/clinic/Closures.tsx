import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CalendarOff, Plus, Trash2 } from 'lucide-react'
import { addDaysISO, dateLong, dayNum, monthName, relativeDateTime, todayISO, weekday } from '@/lib/format'
import { useToast } from '@/lib/toast'
import { Button, ConfirmDialog, EmptyState, ErrorState, Field, Input, ListSkeleton, Modal, PageHeader } from '@/components/ui'
import { useClinic } from './ctx'
import type { Closure } from './types'

interface CreateResult {
  id: string
  date: string
  bookings_to_reschedule: { booking_number: string; doctor: string; start_at: string; patient: string }[]
}

export default function Closures() {
  const { capi, clinicId } = useClinic()
  const qc = useQueryClient()
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [date, setDate] = useState(addDaysISO(todayISO(), 1))
  const [reason, setReason] = useState('')
  const [created, setCreated] = useState<CreateResult | null>(null)
  const [del, setDel] = useState<Closure | null>(null)

  const q = useQuery({ queryKey: ['clinic', clinicId, 'closures'], queryFn: () => capi<Closure[]>('/clinic/closures') })

  const create = useMutation({
    mutationFn: () => capi<CreateResult>('/clinic/closures', { method: 'POST', body: { date, reason: reason.trim() } }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['clinic', clinicId] })
      setOpen(false)
      setReason('')
      if (r.bookings_to_reschedule?.length) setCreated(r)
      else toast.success(`${dateLong(r.date)} dam olish kuni qilib belgilandi`)
    },
    onError: (e) => toast.error(e),
  })
  const remove = useMutation({
    mutationFn: (c: Closure) => capi(`/clinic/closures/${c.id}`, { method: 'DELETE' }),
    onSuccess: () => {
      toast.success("Dam olish kuni o'chirildi")
      setDel(null)
      qc.invalidateQueries({ queryKey: ['clinic', clinicId] })
    },
    onError: (e) => toast.error(e),
  })

  const today = todayISO()
  const list = [...(q.data ?? [])].sort((a, b) => a.date.localeCompare(b.date))
  const upcoming = list.filter((c) => c.date >= today)
  const past = list.filter((c) => c.date < today)

  const Row = ({ c, muted }: { c: Closure; muted?: boolean }) => (
    <li className="flex items-center gap-4 px-5 py-3.5">
      <div className={muted ? 'flex h-14 w-14 shrink-0 flex-col items-center justify-center rounded-2xl bg-ink-100 text-ink-500' : 'flex h-14 w-14 shrink-0 flex-col items-center justify-center rounded-2xl bg-gradient-to-br from-rose-50 to-amber-50 text-rose-700 ring-1 ring-rose-100'}>
        <span className="text-xl font-extrabold leading-none">{dayNum(c.date)}</span>
        <span className="mt-0.5 text-[10px] font-bold uppercase">{monthName(c.date).slice(0, 3)}</span>
      </div>
      <div className="min-w-0 flex-1">
        <div className="font-semibold text-ink-900">{weekday(c.date)}, {dateLong(c.date)}</div>
        <div className="truncate text-sm text-ink-500">{c.reason || 'Sabab ko‘rsatilmagan'}</div>
      </div>
      {!muted && (
        <Button variant="ghost" size="sm" className="text-rose-600 hover:bg-rose-50" aria-label="O'chirish" onClick={() => setDel(c)}>
          <Trash2 className="h-4 w-4" />
        </Button>
      )}
    </li>
  )

  return (
    <div>
      <PageHeader
        title="Dam olish kunlari"
        subtitle="Bayramlar, ta'mir va sanitariya kunlari — bu kunlarga bron qilinmaydi"
        actions={<Button icon={<Plus className="h-4 w-4" />} onClick={() => setOpen(true)}>Kun qo'shish</Button>}
      />
      {q.isLoading ? (
        <ListSkeleton rows={3} />
      ) : q.error ? (
        <div className="card"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
      ) : list.length === 0 ? (
        <div className="card">
          <EmptyState icon={<CalendarOff className="h-7 w-7" />} title="Dam olish kunlari belgilanmagan" text="Klinika yopiq bo'ladigan kunlarni oldindan belgilang — mijozlar bu kunlarga yozila olmaydi." action={<Button onClick={() => setOpen(true)}>Kun qo'shish</Button>} />
        </div>
      ) : (
        <div className="space-y-6">
          <div className="card overflow-hidden">
            <div className="border-b border-ink-100 px-5 py-3 font-bold text-ink-900">Kelgusi ({upcoming.length})</div>
            {upcoming.length ? <ul className="divide-y divide-ink-100">{upcoming.map((c) => <Row key={c.id} c={c} />)}</ul> : <p className="px-5 py-6 text-sm text-ink-500">Kelgusi dam olish kunlari yo'q.</p>}
          </div>
          {past.length > 0 && (
            <div className="card overflow-hidden">
              <div className="border-b border-ink-100 px-5 py-3 font-bold text-ink-500">O'tgan</div>
              <ul className="divide-y divide-ink-100">{past.slice(-10).reverse().map((c) => <Row key={c.id} c={c} muted />)}</ul>
            </div>
          )}
        </div>
      )}

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Dam olish kuni"
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>Bekor</Button>
            <Button loading={create.isPending} disabled={!date || date <= today} onClick={() => create.mutate()}>Saqlash</Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Sana" hint="Faqat kelajakdagi kun">
            <Input type="date" min={addDaysISO(today, 1)} value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label="Sabab">
            <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Masalan: Mustaqillik kuni" maxLength={255} />
          </Field>
          <div className="flex items-start gap-2.5 rounded-2xl bg-amber-50 p-3 text-xs text-amber-800">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            Bu kundagi bo'sh slotlar yopiladi. Mavjud bronlar ro'yxati ko'rsatiladi — ularni mijozlar bilan kelishib ko'chiring.
          </div>
        </div>
      </Modal>

      <Modal open={!!created} onClose={() => setCreated(null)} title="Ko'chirilishi kerak bo'lgan bronlar" footer={<Button onClick={() => setCreated(null)}>Tushunarli</Button>}>
        {created && (
          <div className="space-y-3">
            <p className="text-sm text-ink-600">
              {dateLong(created.date)} dam olish kuni qilindi. Shu kunda <b>{created.bookings_to_reschedule.length}</b> ta tasdiqlangan bron bor — mijozlarga qo'ng'iroq qilib, boshqa kunga ko'chiring.
            </p>
            <ul className="divide-y divide-ink-100 rounded-2xl ring-1 ring-ink-100">
              {created.bookings_to_reschedule.map((b) => (
                <li key={b.booking_number} className="flex items-center justify-between gap-3 px-4 py-3 text-sm">
                  <div>
                    <div className="font-semibold text-ink-900">{b.patient}</div>
                    <div className="text-ink-500">{b.doctor} · {b.booking_number}</div>
                  </div>
                  <span className="whitespace-nowrap font-semibold text-ink-700">{relativeDateTime(b.start_at)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </Modal>

      <ConfirmDialog
        open={!!del}
        onClose={() => setDel(null)}
        onConfirm={() => del && remove.mutate(del)}
        loading={remove.isPending}
        title="Dam olish kunini bekor qilish"
        text={del ? `${dateLong(del.date)} yana ish kuni bo'ladi. Slotlarni qayta ochish uchun shifokorlar jadvalni yangilashi kerak bo'lishi mumkin.` : ''}
        confirmText="Bekor qilish"
        danger
      />
    </div>
  )
}
