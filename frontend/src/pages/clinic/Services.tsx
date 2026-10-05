import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Clock, Pencil, Plus, Tags, Trash2 } from 'lucide-react'
import { ApiError } from '@/lib/api'
import { money, minutes } from '@/lib/format'
import { useSpecializations } from '@/lib/queries'
import { useToast } from '@/lib/toast'
import { Badge, Button, ConfirmDialog, EmptyState, ErrorState, Field, Input, ListSkeleton, Modal, PageHeader, Select, Textarea, cx } from '@/components/ui'
import { useClinic } from './ctx'
import type { ServiceRead } from './types'

interface Draft {
  id?: string
  specialization_id: string
  name: string
  name_ru: string
  description: string
  duration_minutes: string
  price: string
}

const EMPTY: Draft = { specialization_id: '', name: '', name_ru: '', description: '', duration_minutes: '30', price: '' }

export default function Services() {
  const { capi, clinicId } = useClinic()
  const qc = useQueryClient()
  const toast = useToast()
  const specs = useSpecializations()
  const [draft, setDraft] = useState<Draft | null>(null)
  const [del, setDel] = useState<ServiceRead | null>(null)

  const q = useQuery({ queryKey: ['clinic', clinicId, 'services'], queryFn: () => capi<ServiceRead[]>('/clinic/services') })
  const specName = (id: string) => specs.data?.find((s) => s.id === id)?.name ?? '—'

  const remove = useMutation({
    mutationFn: (s: ServiceRead) => capi<{ result: string }>(`/clinic/services/${s.id}`, { method: 'DELETE' }),
    onSuccess: (r) => {
      toast.success(r?.result === 'deactivated' ? "Xizmat bronlarda ishlatilgani uchun nofaol qilindi" : "Xizmat o'chirildi")
      setDel(null)
      qc.invalidateQueries({ queryKey: ['clinic', clinicId, 'services'] })
    },
    onError: (e) => toast.error(e),
  })

  const list = q.data ?? []

  return (
    <div>
      <PageHeader
        title="Xizmatlar"
        subtitle="Klinika darajasidagi xizmatlar — klinikadagi barcha shifokorlar ularni taklif qila oladi"
        actions={<Button icon={<Plus className="h-4 w-4" />} onClick={() => setDraft({ ...EMPTY })}>Xizmat qo'shish</Button>}
      />
      {q.isLoading ? (
        <ListSkeleton rows={4} />
      ) : q.error ? (
        <div className="card"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
      ) : list.length === 0 ? (
        <div className="card">
          <EmptyState icon={<Tags className="h-7 w-7" />} title="Klinika xizmatlari yo'q" text="UZI, analizlar, muolajalar kabi klinika xizmatlarini qo'shing." action={<Button onClick={() => setDraft({ ...EMPTY })}>Xizmat qo'shish</Button>} />
        </div>
      ) : (
        <div className="card divide-y divide-ink-100 overflow-hidden">
          {list.map((s) => (
            <div key={s.id} className={cx('flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center', !s.is_active && 'opacity-60')}>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold text-ink-900">{s.name}</span>
                  {!s.is_active && <Badge>Nofaol</Badge>}
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-500">
                  <span>{specName(s.specialization_id)}</span>
                  <span className="flex items-center gap-1"><Clock className="h-3.5 w-3.5" />{minutes(s.duration_minutes)}</span>
                </div>
              </div>
              <div className="flex items-center justify-between gap-2 sm:justify-end">
                <span className="text-lg font-extrabold tabular-nums text-ink-900">{money(s.price)}</span>
                <div className="flex">
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label="Tahrirlash"
                    onClick={() =>
                      setDraft({ id: s.id, specialization_id: s.specialization_id, name: s.name, name_ru: s.name_ru, description: s.description, duration_minutes: String(s.duration_minutes), price: s.price })
                    }
                  >
                    <Pencil className="h-4 w-4" />
                  </Button>
                  <Button variant="ghost" size="sm" aria-label="O'chirish" className="text-rose-600 hover:bg-rose-50" onClick={() => setDel(s)}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {draft && <ServiceModal draft={draft} onClose={() => setDraft(null)} />}
      <ConfirmDialog
        open={!!del}
        onClose={() => setDel(null)}
        onConfirm={() => del && remove.mutate(del)}
        loading={remove.isPending}
        danger
        title="Xizmatni o'chirish"
        text={`"${del?.name}" o'chiriladi. Agar u bronlarda ishlatilgan bo'lsa, tarix saqlanishi uchun faqat nofaol qilinadi.`}
        confirmText="O'chirish"
      />
    </div>
  )
}

function ServiceModal({ draft, onClose }: { draft: Draft; onClose: () => void }) {
  const { capi, clinicId } = useClinic()
  const qc = useQueryClient()
  const toast = useToast()
  const specs = useSpecializations()
  const [d, setD] = useState<Draft>(draft)
  const [large, setLarge] = useState<{ old_price: string; new_price: string } | null>(null)

  const save = useMutation({
    mutationFn: (confirm: boolean) => {
      const body: Record<string, unknown> = {
        specialization_id: d.specialization_id,
        name: d.name.trim(),
        name_ru: d.name_ru.trim(),
        description: d.description.trim(),
        place: 'clinic',
        duration_minutes: Number(d.duration_minutes),
        price: d.price.replace(/\s/g, ''),
      }
      if (confirm) body.confirm_large_change = true
      if (d.id) return capi<ServiceRead>(`/clinic/services/${d.id}`, { method: 'PATCH', body })
      if (clinicId) body.clinic_id = clinicId
      return capi<ServiceRead>('/clinic/services', { method: 'POST', body })
    },
    onSuccess: () => {
      toast.success(d.id ? 'Xizmat saqlandi' : "Xizmat qo'shildi")
      qc.invalidateQueries({ queryKey: ['clinic', clinicId, 'services'] })
      onClose()
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409 && (e.data as { code?: string })?.code === 'large_price_change') {
        const data = e.data as { old_price: string; new_price: string }
        setLarge({ old_price: data.old_price, new_price: data.new_price })
        return
      }
      toast.error(e)
    },
  })
  const fe = save.error instanceof ApiError ? save.error.fields : {}
  const valid = d.specialization_id && d.name.trim() && Number(d.duration_minutes) >= 5 && /^\d+(\.\d{1,2})?$/.test(d.price.replace(/\s/g, ''))

  return (
    <>
      <Modal
        open={!large}
        onClose={onClose}
        title={d.id ? 'Xizmatni tahrirlash' : 'Yangi xizmat'}
        footer={
          <>
            <Button variant="ghost" onClick={onClose}>Bekor</Button>
            <Button loading={save.isPending} disabled={!valid} onClick={() => save.mutate(false)}>Saqlash</Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Mutaxassislik" error={fe.specialization_id}>
            <Select value={d.specialization_id} onChange={(e) => setD({ ...d, specialization_id: e.target.value })}>
              <option value="">Tanlang…</option>
              {specs.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </Select>
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Nomi (o'zbekcha)" error={fe.name}>
              <Input value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} placeholder="UZI diagnostika" />
            </Field>
            <Field label="Nomi (ruscha)" error={fe.name_ru}>
              <Input value={d.name_ru} onChange={(e) => setD({ ...d, name_ru: e.target.value })} placeholder="УЗИ диагностика" />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Davomiyligi (daq)" error={fe.duration_minutes}>
              <Input type="number" min={5} step={5} value={d.duration_minutes} onChange={(e) => setD({ ...d, duration_minutes: e.target.value })} />
            </Field>
            <Field label="Narxi (so'm)" error={fe.price} hint={d.id ? 'Faqat yangi bronlarga ta‘sir qiladi' : undefined}>
              <Input inputMode="decimal" value={d.price} onChange={(e) => setD({ ...d, price: e.target.value.replace(/[^\d.]/g, '') })} placeholder="150000" />
            </Field>
          </div>
          <Field label="Tavsif">
            <Textarea value={d.description} onChange={(e) => setD({ ...d, description: e.target.value })} placeholder="Xizmat haqida qisqacha (ixtiyoriy)" />
          </Field>
        </div>
      </Modal>
      <ConfirmDialog
        open={!!large}
        onClose={() => setLarge(null)}
        onConfirm={() => {
          save.mutate(true)
          setLarge(null)
        }}
        title="Narx keskin oshmoqda"
        confirmText="Ha, tasdiqlayman"
        loading={save.isPending}
      >
        <div className="flex items-start gap-3 rounded-2xl bg-amber-50 p-4 text-sm text-amber-800 ring-1 ring-amber-200">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
          <div>
            Narx <b>{money(large?.old_price)}</b> dan <b>{money(large?.new_price)}</b> ga — 50% dan ko'proq oshmoqda. Bu faqat yangi bronlarga ta'sir qiladi. Davom etasizmi?
          </div>
        </div>
      </ConfirmDialog>
    </>
  )
}
