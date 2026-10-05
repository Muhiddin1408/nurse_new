import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Building2, Lock, MapPin, Save } from 'lucide-react'
import { ApiError } from '@/lib/api'
import { useToast } from '@/lib/toast'
import { Button, Field, Input, PageHeader, Textarea, Toggle, cx } from '@/components/ui'
import { useClinic, useClinicProfile } from './ctx'
import { WEEKDAY_NAMES } from './shared'
import type { ClinicProfile } from './types'

type Hours = Record<string, [string, string] | null>

export default function Profile() {
  const { capi, clinicId } = useClinic()
  const qc = useQueryClient()
  const toast = useToast()
  const q = useClinicProfile()
  const p = q.data!

  const [description, setDescription] = useState('')
  const [phoneVal, setPhone] = useState('')
  const [photo, setPhoto] = useState('')
  const [hours, setHours] = useState<Hours>({})

  useEffect(() => {
    if (!p) return
    setDescription(p.description || '')
    setPhone(p.phone || '')
    setPhoto(p.photo_url || '')
    const h: Hours = {}
    for (let i = 0; i < 7; i++) h[String(i)] = p.working_hours?.[String(i)] ?? null
    setHours(h)
  }, [p])

  const save = useMutation({
    mutationFn: () => capi<ClinicProfile>('/clinic/profile', { method: 'PATCH', body: { description, phone: phoneVal.trim(), photo_url: photo.trim(), working_hours: hours } }),
    onSuccess: (data) => {
      qc.setQueryData(['clinic', clinicId, 'profile'], data)
      toast.success('Profil saqlandi')
    },
    onError: (e) => toast.error(e),
  })
  const fe = save.error instanceof ApiError ? save.error.fields : {}

  if (!p) return null
  const setDay = (d: string, v: [string, string] | null) => setHours((h) => ({ ...h, [d]: v }))
  const badHours = Object.values(hours).some((v) => v && v[0] >= v[1])

  return (
    <div className="max-w-4xl">
      <PageHeader
        title="Klinika profili"
        subtitle="Mijozlar ko'radigan ma'lumotlar"
        actions={<Button icon={<Save className="h-4 w-4" />} loading={save.isPending} disabled={badHours} onClick={() => save.mutate()}>Saqlash</Button>}
      />
      <div className="grid gap-6 lg:grid-cols-5">
        <div className="space-y-6 lg:col-span-3">
          <div className="card space-y-4 p-5">
            <h3 className="font-bold text-ink-900">Asosiy ma'lumotlar</h3>
            <Field label="Tavsif" error={fe.description} hint="Klinika afzalliklari, uskunalar, yo'nalishlar">
              <Textarea rows={5} value={description} onChange={(e) => setDescription(e.target.value)} />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Telefon" error={fe.phone}>
                <Input value={phoneVal} onChange={(e) => setPhone(e.target.value)} placeholder="+998712000101" />
              </Field>
              <Field label="Rasm havolasi (URL)" error={fe.photo_url}>
                <Input value={photo} onChange={(e) => setPhoto(e.target.value)} placeholder="https://…" />
              </Field>
            </div>
          </div>

          <div className="card p-5">
            <h3 className="mb-1 font-bold text-ink-900">Ish vaqti</h3>
            <p className="mb-4 text-sm text-ink-500">Yopiq kunlarni o'chirib qo'ying.</p>
            <div className="divide-y divide-ink-100">
              {WEEKDAY_NAMES.map((name, i) => {
                const d = String(i)
                const v = hours[d]
                const bad = v && v[0] >= v[1]
                return (
                  <div key={d} className="flex flex-wrap items-center gap-3 py-3">
                    <Toggle checked={!!v} onChange={(on) => setDay(d, on ? ['09:00', '18:00'] : null)} />
                    <span className={cx('w-28 font-semibold', v ? 'text-ink-900' : 'text-ink-400')}>{name}</span>
                    {v ? (
                      <div className="flex items-center gap-2">
                        <input type="time" value={v[0]} onChange={(e) => setDay(d, [e.target.value, v[1]])} className={cx('field !w-auto !rounded-xl !px-3 !py-2 text-sm', bad && '!border-rose-400')} />
                        <span className="text-ink-400">—</span>
                        <input type="time" value={v[1]} onChange={(e) => setDay(d, [v[0], e.target.value])} className={cx('field !w-auto !rounded-xl !px-3 !py-2 text-sm', bad && '!border-rose-400')} />
                      </div>
                    ) : (
                      <span className="text-sm text-ink-400">Dam olish</span>
                    )}
                  </div>
                )
              })}
            </div>
            {badHours && <p className="mt-2 text-sm font-medium text-rose-600">Yopilish vaqti ochilishdan keyin bo'lishi kerak.</p>}
          </div>
        </div>

        <div className="space-y-6 lg:col-span-2">
          <div className="card overflow-hidden">
            <div className="relative h-40 bg-gradient-to-br from-brand-400 to-sky-500">
              {photo && <img src={photo} alt="" className="absolute inset-0 h-full w-full object-cover" onError={(e) => ((e.target as HTMLImageElement).style.display = 'none')} />}
              {!photo && <Building2 className="absolute left-1/2 top-1/2 h-14 w-14 -translate-x-1/2 -translate-y-1/2 text-white/70" />}
            </div>
            <div className="p-5">
              <div className="text-lg font-bold text-ink-900">{p.name}</div>
              <div className="mt-1 flex items-start gap-1.5 text-sm text-ink-500"><MapPin className="mt-0.5 h-4 w-4 shrink-0" />{p.city}, {p.street}</div>
            </div>
          </div>
          <div className="flex gap-3 rounded-3xl bg-ink-100/70 p-4 text-sm text-ink-600">
            <Lock className="mt-0.5 h-4 w-4 shrink-0 text-ink-400" />
            <div>Nomi, manzili va koordinatalari moderatsiya orqali o'zgartiriladi. O'zgartirish uchun qo'llab-quvvatlash xizmatiga murojaat qiling.</div>
          </div>
        </div>
      </div>
    </div>
  )
}
