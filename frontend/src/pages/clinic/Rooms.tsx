import { useState, type KeyboardEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { DoorOpen, Pencil, Plus, X } from 'lucide-react'
import { ApiError } from '@/lib/api'
import { useToast } from '@/lib/toast'
import { Badge, Button, EmptyState, ErrorState, Field, Input, ListSkeleton, Modal, PageHeader, Toggle, cx } from '@/components/ui'
import { useClinic } from './ctx'
import type { Room } from './types'

type Draft = Omit<Room, 'id'> & { id?: string }
const EMPTY: Draft = { name: '', floor: '', equipment: [], is_active: true }

export default function Rooms() {
  const { capi, clinicId } = useClinic()
  const qc = useQueryClient()
  const toast = useToast()
  const [draft, setDraft] = useState<Draft | null>(null)

  const q = useQuery({ queryKey: ['clinic', clinicId, 'rooms'], queryFn: () => capi<Room[]>('/clinic/rooms') })

  const toggle = useMutation({
    mutationFn: (r: Room) => capi<Room>(`/clinic/rooms/${r.id}`, { method: 'PATCH', body: { is_active: !r.is_active } }),
    onSuccess: (r) => {
      toast.success(r.is_active ? 'Xona faollashtirildi' : "Xona o'chirildi")
      qc.invalidateQueries({ queryKey: ['clinic', clinicId, 'rooms'] })
    },
    onError: (e) => toast.error(e),
  })

  return (
    <div>
      <PageHeader
        title="Xonalar"
        subtitle="Kabinetlar, qavat va uskunalar — ish qoidalariga biriktiriladi"
        actions={<Button icon={<Plus className="h-4 w-4" />} onClick={() => setDraft({ ...EMPTY })}>Xona qo'shish</Button>}
      />
      {q.isLoading ? (
        <ListSkeleton rows={3} />
      ) : q.error ? (
        <div className="card"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
      ) : (q.data ?? []).length === 0 ? (
        <div className="card">
          <EmptyState icon={<DoorOpen className="h-7 w-7" />} title="Xona qo'shilmagan" text="Xonalarni qo'shing va ish qoidalariga biriktiring — ikki shifokor bir xonaga tushib qolmaydi." action={<Button onClick={() => setDraft({ ...EMPTY })}>Xona qo'shish</Button>} />
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {q.data!.map((r) => (
            <div key={r.id} className={cx('card p-5 transition', !r.is_active && 'opacity-60')}>
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-center gap-3">
                  <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-50 to-sky-50 text-brand-600 ring-1 ring-brand-100">
                    <DoorOpen className="h-6 w-6" />
                  </span>
                  <div>
                    <div className="font-bold text-ink-900">{r.name}</div>
                    <div className="text-sm text-ink-500">{r.floor ? `${r.floor}-qavat` : 'Qavat ko‘rsatilmagan'}</div>
                  </div>
                </div>
                <Toggle checked={r.is_active} disabled={toggle.isPending} onChange={() => toggle.mutate(r)} />
              </div>
              <div className="mt-4 flex min-h-[28px] flex-wrap gap-1.5">
                {r.equipment.length ? r.equipment.map((e) => <Badge key={e} tone="blue">{e}</Badge>) : <span className="text-sm text-ink-400">Uskunalar ko'rsatilmagan</span>}
              </div>
              <Button variant="ghost" size="sm" className="mt-3 -ml-2" icon={<Pencil className="h-4 w-4" />} onClick={() => setDraft({ ...r })}>Tahrirlash</Button>
            </div>
          ))}
        </div>
      )}
      {draft && <RoomModal draft={draft} onClose={() => setDraft(null)} />}
    </div>
  )
}

function RoomModal({ draft, onClose }: { draft: Draft; onClose: () => void }) {
  const { capi, clinicId } = useClinic()
  const qc = useQueryClient()
  const toast = useToast()
  const [d, setD] = useState<Draft>(draft)
  const [eq, setEq] = useState('')

  const save = useMutation({
    mutationFn: () => {
      const body = { name: d.name.trim(), floor: d.floor.trim(), equipment: d.equipment, is_active: d.is_active }
      return d.id ? capi<Room>(`/clinic/rooms/${d.id}`, { method: 'PATCH', body }) : capi<Room>('/clinic/rooms', { method: 'POST', body })
    },
    onSuccess: () => {
      toast.success(d.id ? 'Xona saqlandi' : "Xona qo'shildi")
      qc.invalidateQueries({ queryKey: ['clinic', clinicId, 'rooms'] })
      onClose()
    },
    onError: (e) => toast.error(e),
  })
  const fe = save.error instanceof ApiError ? save.error.fields : {}

  const addEq = () => {
    const v = eq.trim()
    if (v && !d.equipment.includes(v)) setD({ ...d, equipment: [...d.equipment, v] })
    setEq('')
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      addEq()
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={d.id ? 'Xonani tahrirlash' : 'Yangi xona'}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Bekor</Button>
          <Button loading={save.isPending} disabled={!d.name.trim()} onClick={() => save.mutate()}>Saqlash</Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid grid-cols-3 gap-3">
          <Field label="Nomi" className="col-span-2" error={fe.name}>
            <Input value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} placeholder="101-xona" autoFocus />
          </Field>
          <Field label="Qavat" error={fe.floor}>
            <Input value={d.floor} onChange={(e) => setD({ ...d, floor: e.target.value })} placeholder="1" />
          </Field>
        </div>
        <Field label="Uskunalar" hint="Enter bosib qo'shing: UZI, EKG, rentgen…">
          <div className="field flex flex-wrap items-center gap-1.5 !py-2">
            {d.equipment.map((x) => (
              <span key={x} className="inline-flex items-center gap-1 rounded-lg bg-sky-50 px-2 py-1 text-xs font-semibold text-sky-700">
                {x}
                <button type="button" onClick={() => setD({ ...d, equipment: d.equipment.filter((y) => y !== x) })} className="text-sky-500 hover:text-sky-800">
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
            <input value={eq} onChange={(e) => setEq(e.target.value)} onKeyDown={onKey} onBlur={addEq} className="min-w-[100px] flex-1 bg-transparent py-1 text-[15px] outline-none" placeholder={d.equipment.length ? '' : 'Uskuna nomi'} />
          </div>
        </Field>
        <div className="flex items-center justify-between rounded-2xl bg-ink-50 p-3.5">
          <div>
            <div className="font-semibold text-ink-900">Faol</div>
            <div className="text-xs text-ink-500">O'chirilgan xonaga qabul tayinlanmaydi</div>
          </div>
          <Toggle checked={d.is_active} onChange={(v) => setD({ ...d, is_active: v })} />
        </div>
      </div>
    </Modal>
  )
}
