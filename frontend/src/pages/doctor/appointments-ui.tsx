import { useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, CheckCircle2, Clock, History, Home, MapPin, MessageSquare, Phone, Play, UserX } from 'lucide-react'
import { api } from '@/lib/api'
import { useToast } from '@/lib/toast'
import { dateLong, minutes, phone, relativeDateTime, time } from '@/lib/format'
import { Avatar, Badge, Button, ConfirmDialog, ErrorState, Field, Modal, Skeleton, StatusBadge, Textarea, cx } from '@/components/ui'
import { APPT_STATUS, type Appointment } from './shared'

const GENDER: Record<string, string> = { male: 'Erkak', female: 'Ayol' }

export function patientLine(a: Appointment) {
  const parts = [a.patient.age !== null && a.patient.age !== undefined ? `${a.patient.age} yosh` : null, GENDER[a.patient.gender], a.patient.weight_kg ? `${a.patient.weight_kg} kg` : null]
  return parts.filter(Boolean).join(' · ')
}

export function addressLine(a: Appointment) {
  if (!a.address) return ''
  const x = a.address
  return [x.city, x.street, x.entrance && `${x.entrance}-podyezd`, x.floor && `${x.floor}-qavat`, x.apartment && `kv. ${x.apartment}`].filter(Boolean).join(', ')
}

type Action = 'start' | 'complete' | 'no-show' | 'cancel'

/** Qabul ustidagi amallar: boshlash / yakunlash / kelmadi / bekor qilish. */
export function useAppointmentAction() {
  const qc = useQueryClient()
  const toast = useToast()
  return useMutation({
    mutationFn: ({ id, action, body }: { id: string; action: Action; body?: Record<string, string> }) =>
      api<Appointment>(`/doctor/appointments/${id}/${action}`, { method: 'POST', body: body ?? {} }),
    onSuccess: (_d, v) => {
      const msg: Record<Action, string> = { start: 'Qabul boshlandi', complete: 'Qabul yakunlandi', 'no-show': 'Kelmadi deb belgilandi', cancel: 'Qabul bekor qilindi' }
      toast.success(msg[v.action])
      qc.invalidateQueries({ queryKey: ['doctor', 'appointments'] })
      qc.invalidateQueries({ queryKey: ['doctor', 'earnings'] })
      qc.invalidateQueries({ queryKey: ['doctor', 'schedule'] })
    },
    onError: (e) => toast.error(e),
  })
}

export function AppointmentActions({ a, compact }: { a: Appointment; compact?: boolean }) {
  const m = useAppointmentAction()
  const [dialog, setDialog] = useState<null | 'complete' | 'no-show' | 'cancel'>(null)
  const [note, setNote] = useState('')
  const busy = m.isPending

  if (a.status !== 'confirmed') return null
  const started = !!a.started_at
  const close = () => {
    setDialog(null)
    setNote('')
  }
  const submit = () => {
    if (!dialog) return
    const body: Record<string, string> = dialog === 'cancel' ? { reason: note.trim() } : { note: note.trim() }
    m.mutate({ id: a.id, action: dialog, body }, { onSuccess: close })
  }

  return (
    <>
      <div className={cx('flex flex-wrap gap-2', compact && 'gap-1.5')}>
        {!started ? (
          <Button size="sm" icon={<Play className="h-4 w-4" />} loading={busy && m.variables?.action === 'start'} disabled={busy} onClick={() => m.mutate({ id: a.id, action: 'start' })}>
            Boshlash
          </Button>
        ) : (
          <Button size="sm" icon={<CheckCircle2 className="h-4 w-4" />} disabled={busy} onClick={() => setDialog('complete')}>
            Yakunlash
          </Button>
        )}
        {a.client_phone && (
          <a href={`tel:${a.client_phone}`}>
            <Button size="sm" variant="secondary" icon={<Phone className="h-4 w-4" />}>
              {compact ? '' : "Qo'ng'iroq"}
            </Button>
          </a>
        )}
        <Button size="sm" variant="outline" icon={<UserX className="h-4 w-4" />} disabled={busy} onClick={() => setDialog('no-show')}>
          Kelmadi
        </Button>
        {!started && (
          <Button size="sm" variant="ghost" className="text-rose-600 hover:bg-rose-50 hover:text-rose-700" icon={<Ban className="h-4 w-4" />} disabled={busy} onClick={() => setDialog('cancel')}>
            Bekor
          </Button>
        )}
      </div>

      <ConfirmDialog
        open={!!dialog}
        onClose={close}
        onConfirm={submit}
        loading={busy}
        danger={dialog !== 'complete'}
        title={dialog === 'complete' ? 'Qabulni yakunlash' : dialog === 'no-show' ? 'Bemor kelmadimi?' : 'Qabulni bekor qilish'}
        confirmText={dialog === 'complete' ? 'Yakunlash' : dialog === 'no-show' ? 'Kelmadi deb belgilash' : 'Bekor qilish'}
        text={
          dialog === 'complete'
            ? `${a.patient.full_name} — qabul natijasi bo'yicha qisqa izoh qoldiring (ixtiyoriy).`
            : dialog === 'no-show'
              ? "Bemor qabulga kelmagani qayd etiladi. Bu amalni qaytarib bo'lmaydi."
              : "Mijozga to'lov to'liq (100%) qaytariladi va SMS yuboriladi. Sababni yozing."
        }
      >
        <Field className="mt-4" label={dialog === 'cancel' ? 'Sabab *' : 'Izoh'}>
          <Textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder={dialog === 'cancel' ? 'Masalan: shoshilinch operatsiya' : 'Tashxis, tavsiyalar...'} />
        </Field>
        {dialog === 'cancel' && !note.trim() && <p className="mt-2 text-xs text-ink-500">Sabab majburiy</p>}
      </ConfirmDialog>
    </>
  )
}

export function AppointmentDetailModal({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useQuery({
    queryKey: ['doctor', 'appointments', 'detail', id],
    queryFn: () => api<Appointment>(`/doctor/appointments/${id}`),
    enabled: !!id,
  })
  const a = q.data
  return (
    <Modal open={!!id} onClose={onClose} title={a ? `Qabul #${a.number}` : 'Qabul'} size="lg">
      {q.isLoading && (
        <div className="space-y-3">
          <Skeleton className="h-20" />
          <Skeleton className="h-32" />
        </div>
      )}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {a && (
        <div className="space-y-5">
          <div className="flex items-center gap-4">
            <Avatar name={a.patient.full_name} size={56} />
            <div className="min-w-0 flex-1">
              <div className="text-lg font-bold text-ink-900">{a.patient.full_name}</div>
              <div className="text-sm text-ink-500">{patientLine(a)}</div>
            </div>
            <StatusBadge status={a.status} map={APPT_STATUS} />
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <Info icon={<Clock className="h-4 w-4" />} label="Vaqt" value={`${relativeDateTime(a.start_at)} – ${time(a.end_at)}`} />
            <Info
              icon={a.place === 'home' ? <Home className="h-4 w-4" /> : <MapPin className="h-4 w-4" />}
              label={a.place === 'home' ? 'Uyga chaqiruv' : 'Klinika'}
              value={a.place === 'home' ? addressLine(a) || 'Manzil qabul kuni ochiladi' : a.clinic?.name || '—'}
            />
            <Info
              icon={<Phone className="h-4 w-4" />}
              label="Telefon"
              value={a.client_phone ? <a className="text-brand-700 hover:underline" href={`tel:${a.client_phone}`}>{phone(a.client_phone)}</a> : a.contact_available_from ? `${dateLong(a.contact_available_from)} kuni ochiladi` : '—'}
            />
            <Info icon={<CheckCircle2 className="h-4 w-4" />} label="Xizmatlar" value={a.services.map((s) => `${s.name} (${minutes(s.duration_minutes)})`).join(', ')} />
          </div>

          {a.client_comment && (
            <div className="rounded-2xl bg-sky-50 p-4 text-sm text-sky-900">
              <div className="mb-1 flex items-center gap-2 font-semibold">
                <MessageSquare className="h-4 w-4" /> Mijoz izohi
              </div>
              {a.client_comment}
            </div>
          )}
          {a.address?.comment && <div className="rounded-2xl bg-amber-50 p-4 text-sm text-amber-900">📍 {a.address.comment}</div>}
          {a.note && (
            <div className="rounded-2xl bg-brand-50 p-4 text-sm text-brand-900">
              <div className="mb-1 font-semibold">Shifokor izohi</div>
              {a.note}
            </div>
          )}

          <AppointmentActions a={a} />

          <div>
            <h4 className="mb-2 flex items-center gap-2 font-bold text-ink-900">
              <History className="h-4 w-4 text-ink-400" /> Bemor tarixi
            </h4>
            {a.patient_history && a.patient_history.length > 0 ? (
              <ol className="relative space-y-3 border-l-2 border-ink-100 pl-5">
                {a.patient_history.map((h, i) => (
                  <li key={i} className="relative">
                    <span className="absolute -left-[27px] top-1.5 h-3 w-3 rounded-full border-2 border-white bg-brand-400" />
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="font-semibold text-ink-800">{dateLong(h.date)}</span>
                      <StatusBadge status={h.status} map={APPT_STATUS} />
                    </div>
                    <div className="text-sm text-ink-500">{h.services.join(', ')}</div>
                    {h.note && <div className="mt-1 text-sm text-ink-700">{h.note}</div>}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-sm text-ink-500">Bu bemor sizda birinchi marta.</p>
            )}
          </div>
        </div>
      )}
    </Modal>
  )
}

function Info({ icon, label, value }: { icon: ReactNode; label: string; value: ReactNode }) {
  return (
    <div className="rounded-2xl bg-ink-50 p-3.5">
      <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-ink-400">
        {icon}
        {label}
      </div>
      <div className="text-sm font-medium text-ink-800">{value}</div>
    </div>
  )
}

export function PlaceBadge({ a }: { a: Appointment }) {
  return a.place === 'home' ? (
    <Badge tone="violet">
      <Home className="h-3 w-3" /> Uyda
    </Badge>
  ) : (
    <Badge tone="blue">
      <MapPin className="h-3 w-3" /> {a.clinic?.name ?? 'Klinika'}
    </Badge>
  )
}
