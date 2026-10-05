import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { MapPin, Pencil, Plus, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { useAddresses } from '@/lib/queries'
import { useToast } from '@/lib/toast'
import type { Address } from '@/lib/types'
import { AddressForm } from '@/components/forms'
import { Badge, Button, ConfirmDialog, EmptyState, ErrorState, ListSkeleton, Modal, PageHeader } from '@/components/ui'

export default function AddressesPage() {
  const q = useAddresses()
  const qc = useQueryClient()
  const toast = useToast()
  const [edit, setEdit] = useState<Address | 'new' | null>(null)
  const [del, setDel] = useState<Address | null>(null)

  const remove = useMutation({
    mutationFn: (id: string) => api(`/patient/addresses/${id}/`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['addresses'] })
      toast.success("Manzil o'chirildi")
      setDel(null)
    },
    onError: (e) => toast.error(e),
  })

  return (
    <div>
      <PageHeader title="Manzillar" subtitle="Uyga chaqiruv uchun saqlangan manzillar" actions={<Button icon={<Plus className="h-4 w-4" />} onClick={() => setEdit('new')}>Qo'shish</Button>} />
      {q.isError ? (
        <div className="card"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
      ) : q.isLoading ? (
        <ListSkeleton rows={2} />
      ) : !q.data?.length ? (
        <div className="card">
          <EmptyState icon={<MapPin className="h-7 w-7" />} title="Manzillar yo'q" text="Shifokorni uyga chaqirish uchun manzil qo'shing." action={<Button onClick={() => setEdit('new')}>Manzil qo'shish</Button>} />
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {q.data.map((a) => (
            <div key={a.id} className="card flex items-start gap-4 p-5">
              <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-brand-50 text-brand-600"><MapPin className="h-6 w-6" /></span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-bold text-ink-900">{a.label || 'Manzil'}</span>
                  {a.is_default && <Badge tone="green">Asosiy</Badge>}
                </div>
                <div className="mt-1 text-sm text-ink-600">{a.city}, {a.street}</div>
                <div className="text-xs text-ink-400">
                  {[a.entrance && `${a.entrance}-podyezd`, a.floor && `${a.floor}-qavat`, a.apartment && `${a.apartment}-xonadon`].filter(Boolean).join(' · ')}
                </div>
                {a.comment && <div className="mt-1 text-xs italic text-ink-400">“{a.comment}”</div>}
              </div>
              <div className="flex flex-col gap-1">
                <button onClick={() => setEdit(a)} className="rounded-xl p-2 text-ink-400 hover:bg-ink-100 hover:text-ink-700" aria-label="Tahrirlash"><Pencil className="h-4 w-4" /></button>
                <button onClick={() => setDel(a)} className="rounded-xl p-2 text-ink-400 hover:bg-rose-50 hover:text-rose-600" aria-label="O'chirish"><Trash2 className="h-4 w-4" /></button>
              </div>
            </div>
          ))}
        </div>
      )}
      <Modal open={!!edit} onClose={() => setEdit(null)} title={edit === 'new' ? 'Yangi manzil' : 'Manzilni tahrirlash'}>
        {edit && <AddressForm key={edit === 'new' ? 'new' : edit.id} initial={edit === 'new' ? undefined : edit} onSaved={() => setEdit(null)} />}
      </Modal>
      <ConfirmDialog open={!!del} onClose={() => setDel(null)} onConfirm={() => del && remove.mutate(del.id)} loading={remove.isPending} danger title="Manzilni o'chirasizmi?" text={`${del?.street} manzili o'chiriladi.`} confirmText="O'chirish" />
    </div>
  )
}
