import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Pencil, Plus, Trash2, Users } from 'lucide-react'
import { api } from '@/lib/api'
import { ageFrom, dateLong } from '@/lib/format'
import { usePatients } from '@/lib/queries'
import { useToast } from '@/lib/toast'
import type { Patient } from '@/lib/types'
import { PatientForm } from '@/components/forms'
import { Avatar, Badge, Button, ConfirmDialog, EmptyState, ErrorState, ListSkeleton, Modal, PageHeader } from '@/components/ui'

export default function PatientsPage() {
  const q = usePatients()
  const qc = useQueryClient()
  const toast = useToast()
  const [edit, setEdit] = useState<Patient | 'new' | null>(null)
  const [del, setDel] = useState<Patient | null>(null)

  const remove = useMutation({
    mutationFn: (id: string) => api(`/patient/patients/${id}/`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['patients'] })
      toast.success("Bemor o'chirildi")
      setDel(null)
    },
    onError: (e) => toast.error(e),
  })

  return (
    <div>
      <PageHeader title="Bemorlar" subtitle="Bitta akkauntdan butun oila uchun bron qiling" actions={<Button icon={<Plus className="h-4 w-4" />} onClick={() => setEdit('new')}>Qo'shish</Button>} />
      {q.isError ? (
        <div className="card"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
      ) : q.isLoading ? (
        <ListSkeleton rows={2} />
      ) : !q.data?.length ? (
        <div className="card">
          <EmptyState icon={<Users className="h-7 w-7" />} title="Bemorlar yo'q" text="O'zingizni va oila a'zolaringizni qo'shing." action={<Button onClick={() => setEdit('new')}>Bemor qo'shish</Button>} />
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {q.data.map((p) => (
            <div key={p.id} className="card flex items-center gap-4 p-5">
              <Avatar name={p.full_name} size={52} />
              <div className="min-w-0 flex-1">
                <div className="truncate font-bold text-ink-900">{p.full_name}</div>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-ink-500">
                  {p.relation && <Badge tone="green">{p.relation}</Badge>}
                  <span>{ageFrom(p.birth_date)} yosh</span>
                  <span>·</span>
                  <span>{p.gender === 'male' ? 'Erkak' : 'Ayol'}</span>
                </div>
                <div className="mt-1 text-xs text-ink-400">Tug'ilgan: {dateLong(p.birth_date)}{p.weight_kg ? ` · ${p.weight_kg} kg` : ''}</div>
              </div>
              <div className="flex flex-col gap-1">
                <button onClick={() => setEdit(p)} className="rounded-xl p-2 text-ink-400 hover:bg-ink-100 hover:text-ink-700" aria-label="Tahrirlash"><Pencil className="h-4 w-4" /></button>
                <button onClick={() => setDel(p)} className="rounded-xl p-2 text-ink-400 hover:bg-rose-50 hover:text-rose-600" aria-label="O'chirish"><Trash2 className="h-4 w-4" /></button>
              </div>
            </div>
          ))}
        </div>
      )}
      <Modal open={!!edit} onClose={() => setEdit(null)} title={edit === 'new' ? 'Yangi bemor' : 'Bemorni tahrirlash'}>
        {edit && <PatientForm key={edit === 'new' ? 'new' : edit.id} initial={edit === 'new' ? undefined : edit} onSaved={() => setEdit(null)} />}
      </Modal>
      <ConfirmDialog
        open={!!del}
        onClose={() => setDel(null)}
        onConfirm={() => del && remove.mutate(del.id)}
        loading={remove.isPending}
        danger
        title="Bemorni o'chirasizmi?"
        text={`${del?.full_name} ro'yxatdan olib tashlanadi. Oldingi bronlar tarixi saqlanib qoladi.`}
        confirmText="O'chirish"
      />
    </div>
  )
}
