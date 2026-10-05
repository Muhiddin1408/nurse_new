import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Award, BadgeCheck, Briefcase, Home, Phone, Send, ShieldCheck, Sparkles } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { useToast } from '@/lib/toast'
import { dateLong, phone } from '@/lib/format'
import { Avatar, Badge, Button, ErrorState, Field, Input, PageHeader, Skeleton, Textarea, Toggle, cx } from '@/components/ui'
import { LANGUAGES, SectionCard, useDoctorProfile, useSpecs } from './shared'

interface PublicProfile {
  bio: string
  education: string
  languages: string[]
  accepts_home_visits: boolean
  home_visit_radius_km: number
  follow_up_discount_percent: number
  home_base_latitude: string | null
  home_base_longitude: string | null
  clinic_payment_mode: 'prepaid' | 'deposit' | 'at_clinic'
}

const PAY_MODES: { value: PublicProfile['clinic_payment_mode']; title: string; text: string }[] = [
  { value: 'at_clinic', title: 'Klinikada to‘lov', text: "Mijoz qabulda to'laydi. Bron darhol tasdiqlanadi." },
  { value: 'deposit', title: 'Oldindan depozit', text: 'Bronni ushlab qolish uchun qisman oldindan to‘lov.' },
  { value: 'prepaid', title: "To'liq oldindan", text: "Mijoz bron paytida to'liq to'laydi. Kelmay qolish kamayadi." },
]

export default function ProfilePage() {
  const qc = useQueryClient()
  const toast = useToast()
  const profile = useDoctorProfile()
  const specs = useSpecs()
  // GET /doctor/profile yo'q — bo'sh PATCH joriy ochiq profilni o'zgartirmasdan qaytaradi
  const pub = useQuery({ queryKey: ['doctor', 'public-profile'], queryFn: () => api<PublicProfile>('/doctor/profile', { method: 'PATCH', body: {} }) })
  const tg = useQuery({ queryKey: ['doctor', 'telegram'], queryFn: () => api<{ url: string; expires_in: number; linked: boolean }>('/doctor/telegram/link'), staleTime: 5 * 60_000 })

  const [form, setForm] = useState<PublicProfile | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  useEffect(() => {
    if (pub.data && !form) setForm(pub.data)
  }, [pub.data, form])

  const save = useMutation({
    mutationFn: () => {
      const f = form!
      return api<PublicProfile>('/doctor/profile', {
        method: 'PATCH',
        body: {
          bio: f.bio,
          education: f.education,
          languages: f.languages,
          accepts_home_visits: f.accepts_home_visits,
          home_visit_radius_km: Number(f.home_visit_radius_km),
          follow_up_discount_percent: Number(f.follow_up_discount_percent),
          clinic_payment_mode: f.clinic_payment_mode,
        },
      })
    },
    onSuccess: (d) => {
      toast.success('Profil saqlandi')
      setErrors({})
      setForm(d)
      qc.setQueryData(['doctor', 'public-profile'], d)
      qc.invalidateQueries({ queryKey: ['doctor', 'profile'] })
      qc.invalidateQueries({ queryKey: ['doctor', 'public'] })
    },
    onError: (e) => {
      if (e instanceof ApiError) setErrors(e.fields)
      toast.error(e)
    },
  })

  const p = profile.data
  const dirty = !!form && !!pub.data && JSON.stringify(form) !== JSON.stringify(pub.data)

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title="Profil"
        subtitle="Mijozlar sizni shu ma'lumotlar orqali tanlaydi"
        actions={
          <Button loading={save.isPending} disabled={!dirty} onClick={() => save.mutate()}>
            O'zgarishlarni saqlash
          </Button>
        }
      />

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6">
          <section className="card overflow-hidden">
            <div className="h-24 bg-gradient-to-br from-brand-400 via-brand-600 to-sky-600" />
            <div className="-mt-12 px-6 pb-6">
              {profile.isLoading ? (
                <Skeleton className="h-24 w-24 rounded-3xl" />
              ) : (
                <Avatar name={p?.full_name} size={96} className="rounded-3xl ring-4 ring-white" />
              )}
              <h2 className="mt-3 text-xl font-extrabold text-ink-900">{p?.full_name}</h2>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {(specs.data ?? [])
                  .filter((s) => p?.specialization_ids.includes(s.id))
                  .map((s) => (
                    <Badge key={s.id} tone="green">
                      {s.name}
                    </Badge>
                  ))}
              </div>
              <div className="mt-4 space-y-2.5 text-sm text-ink-600">
                <div className="flex items-center gap-2">
                  <Phone className="h-4 w-4 text-ink-400" /> {phone(p?.phone)}
                </div>
                <div className="flex items-center gap-2">
                  <Briefcase className="h-4 w-4 text-ink-400" /> {p?.experience_years ?? '—'} yil tajriba
                </div>
                <div className="flex items-center gap-2">
                  <Award className="h-4 w-4 text-ink-400" /> Litsenziya: {p?.license_number || '—'}
                </div>
                {p?.license_expires_at && (
                  <div className="flex items-center gap-2">
                    <ShieldCheck className="h-4 w-4 text-ink-400" /> {dateLong(p.license_expires_at)} gacha
                  </div>
                )}
                <div className="flex items-center gap-2 font-semibold text-brand-700">
                  <BadgeCheck className="h-4 w-4" /> Tasdiqlangan shifokor
                </div>
              </div>
              <p className="mt-4 rounded-2xl bg-ink-50 p-3 text-xs text-ink-500">Ism, mutaxassislik va litsenziya faqat moderatsiya orqali o'zgaradi. Qo'llab-quvvatlash xizmatiga murojaat qiling.</p>
            </div>
          </section>

          <section className="card p-6">
            <div className="flex items-center gap-3">
              <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-sky-50 text-sky-600">
                <Send className="h-5 w-5" />
              </div>
              <div className="flex-1">
                <div className="font-bold text-ink-900">Telegram bildirishnomalar</div>
                <div className="text-sm text-ink-500">{tg.data?.linked ? 'Ulangan ✓' : 'Yangi qabullar haqida darhol xabar oling'}</div>
              </div>
            </div>
            {tg.data && !tg.data.linked && (
              <a href={tg.data.url} target="_blank" rel="noreferrer">
                <Button block variant="secondary" className="mt-4" icon={<Send className="h-4 w-4" />}>
                  Telegram'ni ulash
                </Button>
              </a>
            )}
          </section>
        </div>

        <div className="space-y-6 lg:col-span-2">
          {pub.isError && <ErrorState error={pub.error} onRetry={() => pub.refetch()} />}
          {!form && pub.isLoading && <Skeleton className="h-96" />}
          {form && (
            <>
              <SectionCard title="O'zingiz haqingizda">
                <div className="space-y-4">
                  <Field label="Bio" error={errors.bio} hint="Tajribangiz, yo'nalishlaringiz va yondashuvingiz haqida 2–4 gap">
                    <Textarea rows={5} value={form.bio} onChange={(e) => setForm({ ...form, bio: e.target.value })} />
                  </Field>
                  <Field label="Ma'lumot" error={errors.education}>
                    <Textarea rows={2} value={form.education} onChange={(e) => setForm({ ...form, education: e.target.value })} placeholder="Universitet, ordinatura, sertifikatlar" />
                  </Field>
                  <Field label="Qabul tillari" error={errors.languages}>
                    <div className="flex flex-wrap gap-2">
                      {LANGUAGES.map((l) => {
                        const on = form.languages.includes(l.value)
                        return (
                          <button
                            key={l.value}
                            type="button"
                            onClick={() => setForm({ ...form, languages: on ? form.languages.filter((x) => x !== l.value) : [...form.languages, l.value] })}
                            className={cx('rounded-full px-4 py-2 text-sm font-semibold ring-1 transition', on ? 'bg-brand-500 text-white ring-brand-500' : 'bg-white text-ink-600 ring-ink-200 hover:ring-ink-300')}
                          >
                            {l.label}
                          </button>
                        )
                      })}
                    </div>
                  </Field>
                </div>
              </SectionCard>

              <SectionCard title="Uyga chaqiruv">
                <div className="flex items-center justify-between gap-4 rounded-2xl bg-ink-50 p-4">
                  <div className="flex items-center gap-3">
                    <Home className="h-5 w-5 text-violet-500" />
                    <div>
                      <div className="font-semibold text-ink-900">Uyga chaqiruvlarni qabul qilaman</div>
                      <div className="text-sm text-ink-500">Mijozlar sizni uyga chaqira oladi</div>
                    </div>
                  </div>
                  <Toggle checked={form.accepts_home_visits} onChange={(v) => setForm({ ...form, accepts_home_visits: v })} />
                </div>
                {form.accepts_home_visits && (
                  <Field className="mt-4" label={`Xizmat radiusi: ${form.home_visit_radius_km} km`} error={errors.home_visit_radius_km}>
                    <input
                      type="range"
                      min={1}
                      max={50}
                      value={form.home_visit_radius_km}
                      onChange={(e) => setForm({ ...form, home_visit_radius_km: Number(e.target.value) })}
                      className="w-full accent-brand-600"
                    />
                  </Field>
                )}
              </SectionCard>

              <SectionCard title="To'lov va chegirmalar">
                <div className="grid gap-3 sm:grid-cols-3">
                  {PAY_MODES.map((m) => (
                    <button
                      key={m.value}
                      type="button"
                      onClick={() => setForm({ ...form, clinic_payment_mode: m.value })}
                      className={cx('rounded-2xl border-2 p-4 text-left transition', form.clinic_payment_mode === m.value ? 'border-brand-500 bg-brand-50/50' : 'border-ink-100 hover:border-ink-200')}
                    >
                      <div className="font-bold text-ink-900">{m.title}</div>
                      <div className="mt-1 text-xs text-ink-500">{m.text}</div>
                    </button>
                  ))}
                </div>
                <p className="mt-2 text-xs text-ink-500">Uy chaqiruvlari har doim to'liq oldindan to'lanadi.</p>
                <Field className="mt-4" label="Takroriy qabul chegirmasi (%)" error={errors.follow_up_discount_percent} hint="14 kun ichida qayta kelgan mijozga avtomatik chegirma. Chegirmani siz qoplaysiz.">
                  <div className="flex items-center gap-3">
                    <Sparkles className="h-5 w-5 text-amber-500" />
                    <Input type="number" min={0} max={100} className="max-w-[140px]" value={form.follow_up_discount_percent} onChange={(e) => setForm({ ...form, follow_up_discount_percent: Number(e.target.value) })} />
                  </div>
                </Field>
              </SectionCard>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
