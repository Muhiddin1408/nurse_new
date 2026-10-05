import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock, History, Home, Info, MapPin, Pencil, Plus, Stethoscope, Trash2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { useToast } from '@/lib/toast'
import { dateShort, minutes, money } from '@/lib/format'
import { Badge, Button, ConfirmDialog, EmptyState, ErrorState, Field, Input, ListSkeleton, Modal, PageHeader, Segmented, Select, Skeleton, Textarea, Toggle, cx } from '@/components/ui'
import { useDoctorProfile, useSpecs, type ServiceRead } from './shared'

interface PriceHistory {
  old_price: string
  new_price: string
  is_large_change: boolean
  created_at: string
}

type Form = { id?: string; name: string; name_ru: string; description: string; place: 'clinic' | 'home'; duration_minutes: number; price: string; specialization_id: string }

export default function ServicesPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const profile = useDoctorProfile()
  const specs = useSpecs()
  const [filter, setFilter] = useState<'all' | 'clinic' | 'home'>('all')
  const [form, setForm] = useState<Form | null>(null)
  const [origPrice, setOrigPrice] = useState<string | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [large, setLarge] = useState<null | { old_price: string; new_price: string }>(null)
  const [del, setDel] = useState<ServiceRead | null>(null)
  const [historyFor, setHistoryFor] = useState<ServiceRead | null>(null)

  const q = useQuery({ queryKey: ['doctor', 'services'], queryFn: () => api<ServiceRead[]>('/doctor/services') })
  const mySpecs = (specs.data ?? []).filter((s) => profile.data?.specialization_ids.includes(s.id))
  const specName = (id: string) => specs.data?.find((s) => s.id === id)?.name ?? ''

  const invalidate = () => qc.invalidateQueries({ queryKey: ['doctor', 'services'] })

  const save = useMutation({
    mutationFn: ({ confirm }: { confirm?: boolean }) => {
      const f = form!
      const body: Record<string, unknown> = {
        name: f.name.trim(),
        name_ru: f.name_ru.trim(),
        description: f.description.trim(),
        place: f.place,
        duration_minutes: Number(f.duration_minutes),
        price: f.price.replace(/\s/g, ''),
        specialization_id: f.specialization_id,
      }
      if (confirm) body.confirm_large_change = true
      return f.id ? api(`/doctor/services/${f.id}`, { method: 'PATCH', body }) : api('/doctor/services', { method: 'POST', body })
    },
    onSuccess: () => {
      toast.success(form?.id ? 'Xizmat yangilandi' : "Xizmat qo'shildi")
      setForm(null)
      setLarge(null)
      invalidate()
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409 && (e.data as { code?: string })?.code === 'large_price_change') {
        const d = e.data as { old_price: string; new_price: string }
        setLarge({ old_price: d.old_price, new_price: d.new_price })
        return
      }
      if (e instanceof ApiError) setErrors(e.fields)
      toast.error(e)
    },
  })

  const toggle = useMutation({
    mutationFn: (s: ServiceRead) => api<ServiceRead>(`/doctor/services/${s.id}/toggle`, { method: 'PATCH' }),
    onSuccess: (s) => {
      toast.success(s.is_active ? 'Xizmat yoqildi' : "Xizmat o'chirildi — mijozlarga ko'rinmaydi")
      invalidate()
    },
    onError: (e) => toast.error(e),
  })

  const remove = useMutation({
    mutationFn: (s: ServiceRead) => api<{ result: string }>(`/doctor/services/${s.id}`, { method: 'DELETE' }),
    onSuccess: (r) => {
      toast.success(r?.result === 'deactivated' ? "Xizmat bronlarda ishlatilgani uchun faqat nofaol qilindi" : "Xizmat o'chirildi")
      setDel(null)
      invalidate()
    },
    onError: (e) => toast.error(e),
  })

  const list = (q.data ?? []).filter((s) => filter === 'all' || s.place === filter)

  const openNew = () => {
    setErrors({})
    setOrigPrice(null)
    setForm({ name: '', name_ru: '', description: '', place: 'clinic', duration_minutes: 30, price: '', specialization_id: mySpecs[0]?.id ?? '' })
  }

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title="Xizmatlar va narxlar"
        subtitle="Mijozlar bron qilishda shu ro'yxatdan tanlaydi"
        actions={
          <Button icon={<Plus className="h-4 w-4" />} onClick={openNew}>
            Xizmat qo'shish
          </Button>
        }
      />

      <div className="mb-5">
        <Segmented
          size="sm"
          value={filter}
          onChange={setFilter}
          options={[
            { value: 'all', label: 'Hammasi', count: q.data?.length },
            { value: 'clinic', label: 'Klinikada' },
            { value: 'home', label: 'Uyda' },
          ]}
        />
      </div>

      {q.isLoading && <ListSkeleton rows={4} />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && list.length === 0 && (
        <div className="card">
          <EmptyState icon={<Stethoscope className="h-7 w-7" />} title="Xizmatlar yo'q" text="Birinchi xizmatingizni qo'shing — narx va davomiylikni belgilang." action={<Button onClick={openNew}>Xizmat qo'shish</Button>} />
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        {list.map((s) => (
          <div key={s.id} className={cx('card group flex flex-col p-5 transition', !s.is_active && 'opacity-60')}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  {s.place === 'home' ? (
                    <Badge tone="violet">
                      <Home className="h-3 w-3" /> Uyda
                    </Badge>
                  ) : (
                    <Badge tone="blue">
                      <MapPin className="h-3 w-3" /> Klinikada
                    </Badge>
                  )}
                  {!s.is_active && <Badge>Nofaol</Badge>}
                </div>
                <h3 className="mt-2 font-bold text-ink-900">{s.name}</h3>
                <div className="text-xs text-ink-500">{specName(s.specialization_id)}</div>
              </div>
              <Toggle checked={s.is_active} disabled={toggle.isPending} onChange={() => toggle.mutate(s)} />
            </div>
            {s.description && <p className="mt-2 line-clamp-2 text-sm text-ink-500">{s.description}</p>}
            <div className="mt-4 flex items-end justify-between">
              <div>
                <div className="text-xl font-extrabold text-ink-900">{money(s.price)}</div>
                <div className="flex items-center gap-1 text-sm text-ink-500">
                  <Clock className="h-3.5 w-3.5" /> {minutes(s.duration_minutes)}
                </div>
              </div>
              <div className="flex gap-1">
                <button className="rounded-xl p-2 text-ink-400 hover:bg-ink-100 hover:text-ink-700" title="Narx tarixi" onClick={() => setHistoryFor(s)}>
                  <History className="h-4 w-4" />
                </button>
                <button
                  className="rounded-xl p-2 text-ink-400 hover:bg-ink-100 hover:text-ink-700"
                  title="Tahrirlash"
                  onClick={() => {
                    setErrors({})
                    setOrigPrice(s.price)
                    setForm({ id: s.id, name: s.name, name_ru: s.name_ru, description: s.description, place: s.place === 'home' ? 'home' : 'clinic', duration_minutes: s.duration_minutes, price: s.price.replace(/\.00$/, ''), specialization_id: s.specialization_id })
                  }}
                >
                  <Pencil className="h-4 w-4" />
                </button>
                <button className="rounded-xl p-2 text-ink-400 hover:bg-rose-50 hover:text-rose-600" title="O'chirish" onClick={() => setDel(s)}>
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      <Modal
        open={!!form}
        onClose={() => setForm(null)}
        title={form?.id ? 'Xizmatni tahrirlash' : 'Yangi xizmat'}
        footer={
          <>
            <Button variant="ghost" onClick={() => setForm(null)}>
              Bekor
            </Button>
            <Button loading={save.isPending} disabled={!form?.name.trim() || !form?.price || !form?.specialization_id} onClick={() => save.mutate({})}>
              Saqlash
            </Button>
          </>
        }
      >
        {form && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Joy" className="sm:col-span-2">
              <Segmented
                className="w-full"
                value={form.place}
                onChange={(v) => setForm({ ...form, place: v })}
                options={[
                  { value: 'clinic', label: 'Klinikada', icon: <MapPin className="h-4 w-4" /> },
                  { value: 'home', label: 'Uyga chaqiruv', icon: <Home className="h-4 w-4" /> },
                ]}
              />
            </Field>
            <Field label="Nomi *" error={errors.name} className="sm:col-span-2">
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Masalan: Birlamchi konsultatsiya" />
            </Field>
            <Field label="Nomi (rus tilida)" error={errors.name_ru} className="sm:col-span-2">
              <Input value={form.name_ru} onChange={(e) => setForm({ ...form, name_ru: e.target.value })} placeholder="Первичная консультация" />
            </Field>
            <Field label="Mutaxassislik *" error={errors.specialization_id}>
              <Select value={form.specialization_id} onChange={(e) => setForm({ ...form, specialization_id: e.target.value })}>
                {mySpecs.length === 0 && <option value="">—</option>}
                {mySpecs.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Davomiyligi (daq) *" error={errors.duration_minutes}>
              <Input type="number" min={5} step={5} value={form.duration_minutes} onChange={(e) => setForm({ ...form, duration_minutes: Number(e.target.value) })} />
            </Field>
            <Field label="Narxi (so'm) *" error={errors.price} className="sm:col-span-2">
              <Input inputMode="numeric" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value.replace(/[^\d.]/g, '') })} placeholder="150000" />
            </Field>
            {form.id && origPrice && form.price && form.price !== origPrice.replace(/\.00$/, '') && (
              <div className="flex gap-2 rounded-2xl bg-sky-50 p-3 text-sm text-sky-800 sm:col-span-2">
                <Info className="h-4 w-4 shrink-0" />
                Yangi narx faqat yangi bronlarga ta'sir qiladi — mavjud bronlar eski narxda qoladi.
              </div>
            )}
            <Field label="Tavsif" className="sm:col-span-2">
              <Textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Xizmat nimalarni o'z ichiga oladi" />
            </Field>
          </div>
        )}
      </Modal>

      <ConfirmDialog
        open={!!large}
        onClose={() => setLarge(null)}
        onConfirm={() => save.mutate({ confirm: true })}
        loading={save.isPending}
        title="Narx keskin oshmoqda"
        confirmText="Ha, tasdiqlayman"
        text={large ? `Narx ${money(large.old_price)} dan ${money(large.new_price)} ga — 50% dan ko'proq oshmoqda. Bu mijozlar sonini kamaytirishi mumkin. Davom etasizmi?` : ''}
      />

      <ConfirmDialog
        open={!!del}
        onClose={() => setDel(null)}
        onConfirm={() => del && remove.mutate(del)}
        loading={remove.isPending}
        danger
        title="Xizmatni o'chirish"
        confirmText="O'chirish"
        text={del ? `"${del.name}" o'chiriladi. Agar u bronlarda ishlatilgan bo'lsa, faqat nofaol qilinadi.` : ''}
      />

      <PriceHistoryModal service={historyFor} onClose={() => setHistoryFor(null)} />
    </div>
  )
}

function PriceHistoryModal({ service, onClose }: { service: ServiceRead | null; onClose: () => void }) {
  const q = useQuery({
    queryKey: ['doctor', 'services', 'history', service?.id],
    queryFn: () => api<PriceHistory[]>(`/doctor/services/${service!.id}/price-history`),
    enabled: !!service,
  })
  const points = [...(q.data ?? [])].sort((a, b) => a.created_at.localeCompare(b.created_at))
  const series = points.length ? [{ v: Number(points[0].old_price), d: '' }, ...points.map((p) => ({ v: Number(p.new_price), d: p.created_at }))] : []
  const max = Math.max(1, ...series.map((s) => s.v))

  return (
    <Modal open={!!service} onClose={onClose} title={service ? `Narx tarixi — ${service.name}` : ''}>
      {q.isLoading && <Skeleton className="h-40" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && points.length === 0 && <EmptyState icon={<History className="h-7 w-7" />} title="Narx o'zgarmagan" text={`Joriy narx: ${money(service?.price)}`} />}
      {points.length > 0 && (
        <>
          <div className="flex h-40 items-end gap-2 rounded-2xl bg-ink-50 p-4">
            {series.map((s, i) => (
              <div key={i} className="flex flex-1 flex-col items-center gap-1">
                <span className="text-[10px] font-bold text-ink-500">{Math.round(s.v / 1000)}k</span>
                <div className={cx('w-full rounded-t-lg bg-gradient-to-t', i === series.length - 1 ? 'from-brand-600 to-brand-400' : 'from-ink-300 to-ink-200')} style={{ height: `${(s.v / max) * 100}%`, minHeight: 6 }} />
                <span className="text-[10px] text-ink-400">{s.d ? dateShort(s.d) : 'Avval'}</span>
              </div>
            ))}
          </div>
          <div className="mt-4 divide-y divide-ink-100">
            {[...points].reverse().map((p, i) => (
              <div key={i} className="flex items-center justify-between py-2.5 text-sm">
                <span className="text-ink-500">{dateShort(p.created_at)}</span>
                <span>
                  <span className="text-ink-400 line-through">{money(p.old_price)}</span> → <b className="text-ink-900">{money(p.new_price)}</b>
                  {p.is_large_change && <Badge tone="amber" className="ml-2">+50%</Badge>}
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </Modal>
  )
}
