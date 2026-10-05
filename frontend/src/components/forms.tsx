import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { LocateFixed } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { useToast } from '@/lib/toast'
import type { Address, Patient } from '@/lib/types'
import { Button, Field, Input, Segmented, Select, Toggle } from './ui'

const RELATIONS = ["O'zim", 'Turmush o‘rtog‘im', 'Qizim', "O'g'lim", 'Onam', 'Otam', 'Boshqa']

export function PatientForm({ initial, onSaved }: { initial?: Patient; onSaved: (p: Patient) => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [f, setF] = useState({
    full_name: initial?.full_name ?? '',
    relation: initial?.relation ?? 'Qizim',
    birth_date: initial?.birth_date ?? '',
    gender: initial?.gender ?? ('female' as 'male' | 'female'),
    weight_kg: initial?.weight_kg ? String(initial.weight_kg) : '',
  })
  const [errors, setErrors] = useState<Record<string, string>>({})

  const m = useMutation({
    mutationFn: () => {
      const body = { ...f, full_name: f.full_name.trim(), weight_kg: f.weight_kg ? Number(f.weight_kg) : null }
      return initial ? api<Patient>(`/patient/patients/${initial.id}/`, { method: 'PATCH', body }) : api<Patient>('/patient/patients/', { method: 'POST', body })
    },
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ['patients'] })
      toast.success(initial ? 'Saqlandi' : "Bemor qo'shildi")
      onSaved(p)
    },
    onError: (e) => {
      setErrors((e as ApiError).fields ?? {})
      toast.error(e)
    },
  })

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault()
        const errs: Record<string, string> = {}
        if (f.full_name.trim().length < 3) errs.full_name = 'Ism-familiyani kiriting'
        if (!f.birth_date) errs.birth_date = "Tug'ilgan sanani kiriting"
        setErrors(errs)
        if (!Object.keys(errs).length) m.mutate()
      }}
    >
      <Field label="Ism va familiya" error={errors.full_name}>
        <Input value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} placeholder="Masalan: Mohinur Karimova" invalid={!!errors.full_name} />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Kim bo'ladi?" error={errors.relation}>
          <Select value={f.relation} onChange={(e) => setF({ ...f, relation: e.target.value })}>
            {RELATIONS.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </Select>
        </Field>
        <Field label="Tug'ilgan sana" error={errors.birth_date}>
          <Input type="date" max={new Date().toISOString().slice(0, 10)} value={f.birth_date} onChange={(e) => setF({ ...f, birth_date: e.target.value })} invalid={!!errors.birth_date} />
        </Field>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <span className="label">Jinsi</span>
          <Segmented className="w-full" value={f.gender} onChange={(g) => setF({ ...f, gender: g })} options={[{ value: 'male', label: 'Erkak' }, { value: 'female', label: 'Ayol' }]} />
        </div>
        <Field label="Vazni, kg (ixtiyoriy)" error={errors.weight_kg} hint="Bolalar uchun dori dozasini hisoblashda kerak">
          <Input type="number" min={1} max={300} value={f.weight_kg} onChange={(e) => setF({ ...f, weight_kg: e.target.value })} />
        </Field>
      </div>
      <Button type="submit" block size="lg" loading={m.isPending}>
        Saqlash
      </Button>
    </form>
  )
}

export function AddressForm({ initial, onSaved }: { initial?: Address; onSaved: (a: Address) => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [f, setF] = useState({
    label: initial?.label ?? 'Uy',
    city: initial?.city ?? 'Toshkent',
    street: initial?.street ?? '',
    entrance: initial?.entrance ?? '',
    floor: initial?.floor ?? '',
    apartment: initial?.apartment ?? '',
    comment: initial?.comment ?? '',
    latitude: initial?.latitude ?? '',
    longitude: initial?.longitude ?? '',
    is_default: initial?.is_default ?? true,
  })
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [locating, setLocating] = useState(false)

  const locate = () => {
    if (!navigator.geolocation) return toast.error("Geolokatsiya qo'llab-quvvatlanmaydi")
    setLocating(true)
    navigator.geolocation.getCurrentPosition(
      (p) => {
        setF((s) => ({ ...s, latitude: p.coords.latitude.toFixed(6), longitude: p.coords.longitude.toFixed(6) }))
        setLocating(false)
        toast.success('Joylashuv aniqlandi')
      },
      () => {
        setLocating(false)
        toast.error("Joylashuvni aniqlab bo'lmadi — ruxsat bering")
      },
      { timeout: 10000, enableHighAccuracy: true },
    )
  }

  const m = useMutation({
    mutationFn: () => {
      const body = { ...f, street: f.street.trim() }
      return initial ? api<Address>(`/patient/addresses/${initial.id}/`, { method: 'PATCH', body }) : api<Address>('/patient/addresses/', { method: 'POST', body })
    },
    onSuccess: (a) => {
      qc.invalidateQueries({ queryKey: ['addresses'] })
      toast.success('Manzil saqlandi')
      onSaved(a)
    },
    onError: (e) => {
      setErrors((e as ApiError).fields ?? {})
      toast.error(e)
    },
  })

  const located = !!f.latitude && !!f.longitude

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault()
        const errs: Record<string, string> = {}
        if (!f.street.trim()) errs.street = "Ko'cha va uyni kiriting"
        if (!located) errs.latitude = 'Xaritadagi joylashuvni aniqlang — shifokor aynan shu nuqtaga keladi'
        setErrors(errs)
        if (!Object.keys(errs).length) m.mutate()
      }}
    >
      <div className={located ? 'rounded-2xl bg-brand-50 p-4 ring-1 ring-brand-100' : 'rounded-2xl bg-ink-50 p-4'}>
        <div className="flex items-center justify-between gap-3">
          <div className="text-sm">
            <div className="font-semibold text-ink-900">{located ? 'Joylashuv aniqlandi ✓' : 'Joylashuvni aniqlang'}</div>
            <div className="text-ink-500">{located ? `${Number(f.latitude).toFixed(4)}, ${Number(f.longitude).toFixed(4)}` : 'Uy chaqiruvi uchun aniq nuqta kerak'}</div>
          </div>
          <Button type="button" size="sm" variant={located ? 'outline' : 'primary'} loading={locating} onClick={locate} icon={<LocateFixed className="h-4 w-4" />}>
            {located ? 'Yangilash' : 'Aniqlash'}
          </Button>
        </div>
        {errors.latitude && <p className="mt-2 text-sm font-medium text-rose-600">{errors.latitude}</p>}
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Nomi">
          <Select value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })}>
            {['Uy', 'Ish', 'Ota-onamniki', 'Boshqa'].map((l) => (
              <option key={l}>{l}</option>
            ))}
          </Select>
        </Field>
        <Field label="Shahar" error={errors.city}>
          <Input value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} />
        </Field>
      </div>
      <Field label="Ko'cha, uy" error={errors.street}>
        <Input value={f.street} onChange={(e) => setF({ ...f, street: e.target.value })} placeholder="Chilonzor 9-kvartal, 14-uy" invalid={!!errors.street} />
      </Field>
      <div className="grid grid-cols-3 gap-3">
        <Field label="Podyezd">
          <Input value={f.entrance} onChange={(e) => setF({ ...f, entrance: e.target.value })} />
        </Field>
        <Field label="Qavat">
          <Input value={f.floor} onChange={(e) => setF({ ...f, floor: e.target.value })} />
        </Field>
        <Field label="Xonadon">
          <Input value={f.apartment} onChange={(e) => setF({ ...f, apartment: e.target.value })} />
        </Field>
      </div>
      <Field label="Izoh (ixtiyoriy)">
        <Input value={f.comment} onChange={(e) => setF({ ...f, comment: e.target.value })} placeholder="Domofon kodi, mo'ljal..." />
      </Field>
      <label className="flex items-center justify-between rounded-2xl bg-ink-50 p-4">
        <span className="font-semibold text-ink-800">Asosiy manzil</span>
        <Toggle checked={f.is_default} onChange={(v) => setF({ ...f, is_default: v })} />
      </label>
      <Button type="submit" block size="lg" loading={m.isPending}>
        Saqlash
      </Button>
    </form>
  )
}
