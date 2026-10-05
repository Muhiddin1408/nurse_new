import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pause, Play, Star, UserPlus, Users, XCircle } from 'lucide-react'
import { ApiError } from '@/lib/api'
import { useToast } from '@/lib/toast'
import { Avatar, Badge, Button, ConfirmDialog, EmptyState, ErrorState, Field, Input, ListSkeleton, Modal, PageHeader, Segmented, StatusBadge } from '@/components/ui'
import { useClinic } from './ctx'
import { AFFILIATION_STATUS, PhoneField } from './shared'
import type { ClinicDoctorRow } from './types'

type Action = 'pause' | 'resume' | 'end'
const ACTION_TEXT: Record<Action, { title: string; text: string; confirm: string; danger?: boolean }> = {
  pause: {
    title: "Hamkorlikni to'xtatish",
    text: "Shifokor vaqtincha klinika nomidan yangi bron qabul qilmaydi. Mavjud bronlar saqlanib qoladi. Istalgan vaqtda qayta tiklash mumkin.",
    confirm: "To'xtatish",
  },
  resume: { title: 'Hamkorlikni tiklash', text: 'Shifokor yana klinikada bron qabul qila boshlaydi.', confirm: 'Tiklash' },
  end: {
    title: 'Hamkorlikni tugatish',
    text: "Bu amal qaytarilmaydi: shifokor klinikadan chiqariladi. Tasdiqlangan bronlar saqlanadi, lekin yangi bron qabul qilinmaydi.",
    confirm: 'Tugatish',
    danger: true,
  },
}

export default function Doctors() {
  const { capi, clinicId } = useClinic()
  const qc = useQueryClient()
  const toast = useToast()
  const [filter, setFilter] = useState<'all' | 'active' | 'invited' | 'paused' | 'ended'>('all')
  const [inviteOpen, setInviteOpen] = useState(false)
  const [pending, setPending] = useState<{ row: ClinicDoctorRow; action: Action } | null>(null)

  const q = useQuery({
    queryKey: ['clinic', clinicId, 'doctors'],
    queryFn: () => capi<ClinicDoctorRow[]>('/clinic/doctors'),
  })

  const act = useMutation({
    mutationFn: ({ row, action }: { row: ClinicDoctorRow; action: Action }) =>
      capi<{ affiliation_id: string; status: string; active_bookings_kept: number }>(`/clinic/doctors/${row.affiliation_id}/${action}`, { method: 'POST' }),
    onSuccess: (r) => {
      toast.success(
        `Holat: ${AFFILIATION_STATUS[r.status as keyof typeof AFFILIATION_STATUS]?.label ?? r.status}` +
          (r.active_bookings_kept ? ` · ${r.active_bookings_kept} ta faol bron saqlandi` : ''),
      )
      setPending(null)
      qc.invalidateQueries({ queryKey: ['clinic', clinicId] })
    },
    onError: (e) => toast.error(e),
  })

  const rows = (q.data ?? []).filter((r) => filter === 'all' || r.status === filter)
  const count = (s: string) => (q.data ?? []).filter((r) => r.status === s).length

  return (
    <div>
      <PageHeader
        title="Shifokorlar"
        subtitle="Klinika bilan hamkorlik qilayotgan shifokorlar va takliflar"
        actions={<Button icon={<UserPlus className="h-4 w-4" />} onClick={() => setInviteOpen(true)}>Shifokor taklif qilish</Button>}
      />
      <div className="mb-5 overflow-x-auto scrollbar-none">
        <Segmented
          value={filter}
          onChange={setFilter}
          size="sm"
          options={[
            { value: 'all', label: 'Barchasi' },
            { value: 'active', label: 'Faol', count: count('active') },
            { value: 'invited', label: 'Taklif', count: count('invited') },
            { value: 'paused', label: "To'xtatilgan" },
            { value: 'ended', label: 'Tugatilgan' },
          ]}
        />
      </div>

      {q.isLoading ? (
        <ListSkeleton rows={4} />
      ) : q.error ? (
        <div className="card"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
      ) : rows.length === 0 ? (
        <div className="card">
          <EmptyState
            icon={<Users className="h-7 w-7" />}
            title="Shifokor yo'q"
            text="Telefon raqami orqali shifokorni klinikaga taklif qiling — u kabinetida taklifni qabul qiladi."
            action={<Button icon={<UserPlus className="h-4 w-4" />} onClick={() => setInviteOpen(true)}>Taklif qilish</Button>}
          />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-3">
          {rows.map((r) => (
            <div key={r.affiliation_id} className="card flex min-w-0 flex-col p-5">
              <div className="flex items-start gap-3.5">
                <Avatar name={r.doctor} size={52} />
                <div className="min-w-0 flex-1">
                  <div className="truncate font-bold text-ink-900">{r.doctor}</div>
                  <div className="truncate text-sm text-ink-500">{r.position || r.specializations.join(', ') || '—'}</div>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <StatusBadge status={r.status} map={AFFILIATION_STATUS} />
                    {r.doctor_status !== 'approved' && <Badge tone="amber">Profil: {r.doctor_status}</Badge>}
                  </div>
                </div>
                {Number(r.rating) > 0 && (
                  <span className="chip shrink-0 bg-amber-50 text-amber-700">
                    <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-400" />
                    {Number(r.rating).toFixed(1)}
                    <span className="font-medium text-amber-600/70">({r.reviews_count})</span>
                  </span>
                )}
              </div>
              {r.specializations.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {r.specializations.map((s) => (
                    <span key={s} className="chip bg-ink-50 text-ink-600">{s}</span>
                  ))}
                </div>
              )}
              <div className="mt-auto flex flex-wrap gap-2 pt-4">
                {r.status === 'active' && (
                  <Button variant="outline" size="sm" icon={<Pause className="h-4 w-4" />} onClick={() => setPending({ row: r, action: 'pause' })}>To'xtatish</Button>
                )}
                {r.status === 'paused' && (
                  <Button variant="secondary" size="sm" icon={<Play className="h-4 w-4" />} onClick={() => setPending({ row: r, action: 'resume' })}>Tiklash</Button>
                )}
                {['active', 'paused', 'invited'].includes(r.status) && (
                  <Button variant="ghost" size="sm" className="text-rose-600 hover:bg-rose-50 hover:text-rose-700" icon={<XCircle className="h-4 w-4" />} onClick={() => setPending({ row: r, action: 'end' })}>
                    {r.status === 'invited' ? 'Taklifni bekor qilish' : 'Tugatish'}
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      <InviteModal open={inviteOpen} onClose={() => setInviteOpen(false)} />

      {pending && (
        <ConfirmDialog
          open
          onClose={() => setPending(null)}
          onConfirm={() => act.mutate(pending)}
          loading={act.isPending}
          title={ACTION_TEXT[pending.action].title}
          text={ACTION_TEXT[pending.action].text}
          confirmText={ACTION_TEXT[pending.action].confirm}
          danger={ACTION_TEXT[pending.action].danger}
        >
          <div className="mt-4 flex items-center gap-3 rounded-2xl bg-ink-50 p-3">
            <Avatar name={pending.row.doctor} size={40} className="rounded-xl" />
            <div className="font-semibold text-ink-900">{pending.row.doctor}</div>
          </div>
        </ConfirmDialog>
      )}
    </div>
  )
}

function InviteModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { capi, clinicId } = useClinic()
  const qc = useQueryClient()
  const toast = useToast()
  const [digits, setDigits] = useState('')
  const [position, setPosition] = useState('')

  const m = useMutation({
    mutationFn: () => capi<ClinicDoctorRow>('/clinic/doctors/invite', { method: 'POST', body: { phone: `+998${digits}`, position } }),
    onSuccess: (r) => {
      toast.success(`${r.doctor} ga taklif yuborildi`)
      qc.invalidateQueries({ queryKey: ['clinic', clinicId, 'doctors'] })
      setDigits('')
      setPosition('')
      onClose()
    },
    onError: (e) => toast.error(e),
  })

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Shifokorni taklif qilish"
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Bekor</Button>
          <Button loading={m.isPending} disabled={digits.length !== 9} onClick={() => m.mutate()}>Taklif yuborish</Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-ink-500">Shifokor Turon Clinic'da ro'yxatdan o'tgan bo'lishi kerak. U taklifni o'z kabinetida qabul qiladi.</p>
        <Field label="Telefon raqami" error={m.error instanceof ApiError ? m.error.fields.phone : undefined}>
          <PhoneField value={digits} onChange={setDigits} autoFocus />
        </Field>
        <Field label="Lavozim" hint="Masalan: Bosh shifokor, Kardiolog">
          <Input value={position} onChange={(e) => setPosition(e.target.value)} placeholder="Lavozim (ixtiyoriy)" maxLength={100} />
        </Field>
      </div>
    </Modal>
  )
}
