import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle, ArrowLeft, ArrowRight, Check, CheckCircle2, Clock, FileText, Hourglass, Send, ShieldOff, Trash2, Upload, UserRound } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { useToast } from '@/lib/toast'
import { dateLong, relativeDateTime } from '@/lib/format'
import { Logo } from '@/components/Logo'
import { UserMenu } from '@/components/layout/UserMenu'
import { Button, ErrorState, Field, Input, Select, Skeleton, Textarea, Toggle, cx } from '@/components/ui'
import { LANGUAGES, MISSING_LABEL, useDoctorProfile, useSpecs, type DoctorProfile, type OnboardingStatus } from './shared'

interface Doc {
  id: string
  kind: string
  original_name: string
  content_type: string
  size: number
  created_at: string
  download_url: string
}

const DOC_KINDS: { value: string; label: string; required?: boolean }[] = [
  { value: 'diploma', label: 'Diplom', required: true },
  { value: 'license', label: 'Litsenziya', required: true },
  { value: 'certificate', label: 'Sertifikat' },
  { value: 'passport', label: 'Pasport' },
]

export function OnboardingShell({ children }: { children: ReactNode }) {
  return (
    <div className="mesh-bg min-h-screen">
      <header className="container-x flex h-[72px] items-center justify-between">
        <Logo />
        <UserMenu />
      </header>
      <main className="container-x pb-16 pt-4">{children}</main>
    </div>
  )
}

export function StatusScreen({ status }: { status: OnboardingStatus }) {
  if (status.status === 'pending') {
    return (
      <OnboardingShell>
        <div className="mx-auto max-w-xl">
          <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} className="card p-8 text-center sm:p-10">
            <div className="relative mx-auto mb-6 flex h-20 w-20 items-center justify-center">
              <span className="absolute inset-0 animate-ping rounded-full bg-brand-200 opacity-40" />
              <span className="relative flex h-20 w-20 items-center justify-center rounded-full bg-gradient-to-br from-brand-400 to-brand-600 text-white shadow-glow">
                <Hourglass className="h-9 w-9" />
              </span>
            </div>
            <h1 className="text-2xl font-extrabold">Anketangiz moderatsiyada</h1>
            <p className="mt-3 text-ink-600">Mutaxassislarimiz hujjatlaringizni tekshirmoqda. Odatda bu 1–2 ish kunini oladi. Natija haqida SMS orqali xabar beramiz.</p>
            <div className="mt-6 grid gap-3 text-left sm:grid-cols-2">
              <div className="rounded-2xl bg-ink-50 p-4">
                <div className="text-xs font-semibold uppercase text-ink-400">Yuborilgan</div>
                <div className="font-bold">{status.submitted_at ? relativeDateTime(status.submitted_at) : '—'}</div>
              </div>
              <div className="rounded-2xl bg-brand-50 p-4">
                <div className="text-xs font-semibold uppercase text-brand-600">Javob muddati</div>
                <div className="font-bold text-brand-800">{status.sla_due_at ? relativeDateTime(status.sla_due_at) : '1–2 ish kuni'}</div>
              </div>
            </div>
            <ol className="mt-8 space-y-3 text-left">
              {[
                ['Anketa yuborildi', true],
                ['Hujjatlar tekshirilmoqda', false],
                ['Kabinet ochiladi', false],
              ].map(([t, done], i) => (
                <li key={i} className="flex items-center gap-3">
                  <span className={cx('flex h-8 w-8 items-center justify-center rounded-full text-sm font-bold', done ? 'bg-brand-500 text-white' : i === 1 ? 'bg-amber-100 text-amber-700' : 'bg-ink-100 text-ink-400')}>
                    {done ? <Check className="h-4 w-4" /> : i + 1}
                  </span>
                  <span className={cx('font-semibold', done ? 'text-ink-900' : 'text-ink-500')}>{t as string}</span>
                </li>
              ))}
            </ol>
            <p className="mt-6 text-xs text-ink-400">Moderatsiya davomida anketani tahrirlab bo'lmaydi.</p>
          </motion.div>
        </div>
      </OnboardingShell>
    )
  }
  return null
}

export function SuspendedBanner({ reason }: { reason: string }) {
  return (
    <div className="border-b border-rose-200 bg-rose-50 px-4 py-3 sm:px-6 lg:px-8">
      <div className="flex items-start gap-3 text-sm text-rose-800">
        <ShieldOff className="mt-0.5 h-5 w-5 shrink-0" />
        <div>
          <b>Faoliyatingiz vaqtincha to'xtatilgan.</b> {reason && <>Sabab: {reason}. </>}Yangi qabullar qabul qilinmaydi, faqat moliyaviy ma'lumotlar ochiq. Qo'llab-quvvatlash: +998 71 200-01-01
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
type Step = 0 | 1 | 2 | 3

export function OnboardingWizard({ status }: { status: OnboardingStatus }) {
  const qc = useQueryClient()
  const toast = useToast()
  const profile = useDoctorProfile()
  const specs = useSpecs()
  const docs = useQuery({ queryKey: ['doctor', 'documents'], queryFn: () => api<Doc[]>('/doctor/onboarding/documents') })
  const [step, setStep] = useState<Step>(0)
  const [form, setForm] = useState<Partial<DoctorProfile> | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})

  useEffect(() => {
    if (profile.data && !form) setForm(profile.data)
  }, [profile.data, form])

  const saveProfile = useMutation({
    mutationFn: () => {
      const f = form!
      return api<DoctorProfile>('/doctor/onboarding/profile', {
        method: 'PATCH',
        body: {
          full_name: f.full_name,
          specialization_ids: f.specialization_ids,
          bio: f.bio,
          experience_years: Number(f.experience_years || 0),
          education: f.education,
          languages: f.languages,
          license_number: f.license_number,
          license_expires_at: f.license_expires_at || null,
          accepts_home_visits: f.accepts_home_visits,
          home_visit_radius_km: Number(f.home_visit_radius_km || 10),
        },
      })
    },
    onSuccess: (d) => {
      qc.setQueryData(['doctor', 'profile'], d)
      qc.invalidateQueries({ queryKey: ['doctor', 'onboarding-status'] })
      setErrors({})
    },
    onError: (e) => {
      if (e instanceof ApiError) setErrors(e.fields)
      toast.error(e)
    },
  })

  const submit = useMutation({
    mutationFn: () => api<OnboardingStatus>('/doctor/onboarding/submit', { method: 'POST' }),
    onSuccess: (s) => {
      qc.setQueryData(['doctor', 'onboarding-status'], s)
      toast.success('Anketa moderatsiyaga yuborildi!')
    },
    onError: (e) => {
      toast.error(e)
      qc.invalidateQueries({ queryKey: ['doctor', 'onboarding-status'] })
    },
  })

  const next = async () => {
    if (step < 2) {
      await saveProfile.mutateAsync().catch(() => null).then((r) => r && setStep((s) => (s + 1) as Step))
    } else setStep((s) => Math.min(3, s + 1) as Step)
  }

  const steps = [
    { title: "Shaxsiy ma'lumot", icon: UserRound },
    { title: 'Litsenziya', icon: FileText },
    { title: 'Hujjatlar', icon: Upload },
    { title: 'Yuborish', icon: Send },
  ]

  if (profile.isError) return <OnboardingShell><ErrorState error={profile.error} onRetry={() => profile.refetch()} /></OnboardingShell>

  return (
    <OnboardingShell>
      <div className="mx-auto max-w-3xl">
        <div className="mb-6 text-center">
          <span className="chip bg-brand-100 text-brand-700">Shifokor anketasi</span>
          <h1 className="mt-3 text-3xl font-extrabold sm:text-4xl">Turon Clinic jamoasiga qo'shiling</h1>
          <p className="mt-2 text-ink-500">4 qadam — taxminan 5 daqiqa. Ma'lumotlar har qadamda saqlanadi.</p>
        </div>

        {status.status === 'rejected' && (
          <div className="mb-5 flex gap-3 rounded-3xl bg-rose-50 p-4 text-sm text-rose-800 ring-1 ring-rose-200">
            <AlertTriangle className="h-5 w-5 shrink-0" />
            <div>
              <b>Anketa rad etildi.</b> {status.reason && <>Sabab: {status.reason}. </>}Kamchiliklarni tuzatib, qayta yuboring.
            </div>
          </div>
        )}

        <div className="mb-6 grid grid-cols-4 gap-2">
          {steps.map((s, i) => (
            <button key={s.title} onClick={() => i < step && setStep(i as Step)} className="flex flex-col items-center gap-2 text-center">
              <span className={cx('flex h-11 w-11 items-center justify-center rounded-2xl transition', i < step ? 'bg-brand-500 text-white' : i === step ? 'bg-white text-brand-600 shadow-lift ring-2 ring-brand-400' : 'bg-white/60 text-ink-400 ring-1 ring-ink-100')}>
                {i < step ? <Check className="h-5 w-5" /> : <s.icon className="h-5 w-5" />}
              </span>
              <span className={cx('hidden text-xs font-semibold sm:block', i === step ? 'text-ink-900' : 'text-ink-400')}>{s.title}</span>
            </button>
          ))}
        </div>

        <div className="card p-6 sm:p-8">
          {!form ? (
            <Skeleton className="h-72" />
          ) : (
            <AnimatePresence mode="wait">
              <motion.div key={step} initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} transition={{ duration: 0.2 }}>
                {step === 0 && (
                  <div className="grid gap-4 sm:grid-cols-2">
                    <Field label="To'liq ism (F.I.Sh.) *" error={errors.full_name} className="sm:col-span-2">
                      <Input value={form.full_name ?? ''} onChange={(e) => setForm({ ...form, full_name: e.target.value })} placeholder="Karimova Aziza Rustamovna" />
                    </Field>
                    <Field label="Mutaxassislik *" error={errors.specialization_ids} className="sm:col-span-2">
                      <div className="flex flex-wrap gap-2">
                        {specs.isLoading && <Skeleton className="h-10 w-full" />}
                        {specs.data?.map((s) => {
                          const on = form.specialization_ids?.includes(s.id)
                          return (
                            <button
                              type="button"
                              key={s.id}
                              onClick={() => setForm({ ...form, specialization_ids: on ? form.specialization_ids!.filter((x) => x !== s.id) : [...(form.specialization_ids ?? []), s.id] })}
                              className={cx('rounded-full px-4 py-2 text-sm font-semibold ring-1 transition', on ? 'bg-brand-500 text-white ring-brand-500' : 'bg-white text-ink-600 ring-ink-200 hover:ring-ink-300')}
                            >
                              {on && <Check className="mr-1 inline h-3.5 w-3.5" />}
                              {s.name}
                            </button>
                          )
                        })}
                      </div>
                    </Field>
                    <Field label="Tajriba (yil) *" error={errors.experience_years}>
                      <Input type="number" min={0} max={70} value={form.experience_years ?? 0} onChange={(e) => setForm({ ...form, experience_years: Number(e.target.value) })} />
                    </Field>
                    <Field label="Qabul tillari" error={errors.languages}>
                      <div className="flex flex-wrap gap-1.5">
                        {LANGUAGES.map((l) => {
                          const on = form.languages?.includes(l.value)
                          return (
                            <button
                              key={l.value}
                              type="button"
                              onClick={() => setForm({ ...form, languages: on ? form.languages!.filter((x) => x !== l.value) : [...(form.languages ?? []), l.value] })}
                              className={cx('rounded-xl px-3 py-2 text-sm font-semibold transition', on ? 'bg-ink-900 text-white' : 'bg-ink-100 text-ink-600 hover:bg-ink-200')}
                            >
                              {l.label}
                            </button>
                          )
                        })}
                      </div>
                    </Field>
                    <Field label="Ma'lumot" error={errors.education} className="sm:col-span-2">
                      <Input value={form.education ?? ''} onChange={(e) => setForm({ ...form, education: e.target.value })} placeholder="Toshkent tibbiyot akademiyasi, 2012" />
                    </Field>
                    <Field label="O'zingiz haqingizda" error={errors.bio} className="sm:col-span-2" hint="Mijozlar shu matnni profilingizda ko'radi">
                      <Textarea rows={4} value={form.bio ?? ''} onChange={(e) => setForm({ ...form, bio: e.target.value })} />
                    </Field>
                  </div>
                )}

                {step === 1 && (
                  <div className="grid gap-4 sm:grid-cols-2">
                    <Field label="Litsenziya raqami *" error={errors.license_number}>
                      <Input value={form.license_number ?? ''} onChange={(e) => setForm({ ...form, license_number: e.target.value })} placeholder="LIC-00000" />
                    </Field>
                    <Field label="Amal qilish muddati" error={errors.license_expires_at}>
                      <Input type="date" value={form.license_expires_at ?? ''} onChange={(e) => setForm({ ...form, license_expires_at: e.target.value || null })} />
                    </Field>
                    <div className="flex items-center justify-between gap-4 rounded-2xl bg-ink-50 p-4 sm:col-span-2">
                      <div>
                        <div className="font-semibold text-ink-900">Uyga chaqiruvlarni qabul qilaman</div>
                        <div className="text-sm text-ink-500">Mijozlar sizni uyiga chaqira oladi</div>
                      </div>
                      <Toggle checked={!!form.accepts_home_visits} onChange={(v) => setForm({ ...form, accepts_home_visits: v })} />
                    </div>
                    {form.accepts_home_visits && (
                      <Field label={`Xizmat radiusi: ${form.home_visit_radius_km ?? 10} km`} className="sm:col-span-2">
                        <input type="range" min={1} max={50} value={form.home_visit_radius_km ?? 10} onChange={(e) => setForm({ ...form, home_visit_radius_km: Number(e.target.value) })} className="w-full accent-brand-600" />
                      </Field>
                    )}
                  </div>
                )}

                {step === 2 && <DocumentsStep docs={docs.data ?? []} loading={docs.isLoading} />}

                {step === 3 && (
                  <div className="text-center">
                    <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-3xl bg-gradient-to-br from-brand-400 to-brand-600 text-white shadow-glow">
                      <Send className="h-7 w-7" />
                    </div>
                    <h2 className="text-xl font-extrabold">Hammasi tayyormi?</h2>
                    <p className="mx-auto mt-2 max-w-md text-ink-500">Yuborganingizdan keyin moderator 1–2 ish kunida tekshiradi. Bu vaqtda anketani tahrirlab bo'lmaydi.</p>
                    {status.missing.length > 0 ? (
                      <div className="mx-auto mt-5 max-w-md rounded-2xl bg-amber-50 p-4 text-left text-sm text-amber-900">
                        <div className="mb-2 font-bold">To'ldirilmagan:</div>
                        <ul className="space-y-1">
                          {status.missing.map((m) => (
                            <li key={m} className="flex items-center gap-2">
                              <AlertTriangle className="h-4 w-4" /> {MISSING_LABEL[m] ?? m}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : (
                      <div className="mx-auto mt-5 flex max-w-md items-center justify-center gap-2 rounded-2xl bg-brand-50 p-3 text-sm font-semibold text-brand-700">
                        <CheckCircle2 className="h-5 w-5" /> Barcha majburiy maydonlar to'ldirilgan
                      </div>
                    )}
                  </div>
                )}
              </motion.div>
            </AnimatePresence>
          )}

          <div className="mt-8 flex items-center justify-between gap-3 border-t border-ink-100 pt-5">
            <Button variant="ghost" icon={<ArrowLeft className="h-4 w-4" />} disabled={step === 0} onClick={() => setStep((s) => (s - 1) as Step)}>
              Orqaga
            </Button>
            {step < 3 ? (
              <Button icon={<ArrowRight className="h-4 w-4" />} loading={saveProfile.isPending} onClick={next} className="flex-row-reverse">
                Davom etish
              </Button>
            ) : (
              <Button icon={<Send className="h-4 w-4" />} loading={submit.isPending} onClick={() => submit.mutate()}>
                Moderatsiyaga yuborish
              </Button>
            )}
          </div>
        </div>
        <p className="mt-6 text-center text-sm text-ink-500">
          Savollar bo'lsa: <b>+998 71 200-01-01</b> · <Link to="/" className="font-semibold text-brand-700 hover:underline">Bosh sahifaga qaytish</Link>
        </p>
      </div>
    </OnboardingShell>
  )
}

function DocumentsStep({ docs, loading }: { docs: Doc[]; loading: boolean }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [kind, setKind] = useState('diploma')
  const inputRef = useRef<HTMLInputElement>(null)

  const upload = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData()
      fd.append('kind', kind)
      fd.append('file', file)
      return api<Doc>('/doctor/onboarding/documents', { method: 'POST', body: fd })
    },
    onSuccess: () => {
      toast.success('Hujjat yuklandi')
      qc.invalidateQueries({ queryKey: ['doctor', 'documents'] })
      qc.invalidateQueries({ queryKey: ['doctor', 'onboarding-status'] })
    },
    onError: (e) => toast.error(e),
  })
  const remove = useMutation({
    mutationFn: (id: string) => api(`/doctor/onboarding/documents/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['doctor', 'documents'] })
      qc.invalidateQueries({ queryKey: ['doctor', 'onboarding-status'] })
    },
    onError: (e) => toast.error(e),
  })

  const onFile = (f?: File | null) => {
    if (!f) return
    if (f.size > 10 * 1024 * 1024) return toast.error('Fayl hajmi 10 MB dan oshmasin')
    upload.mutate(f)
  }

  return (
    <div>
      <div className="mb-4 grid gap-3 sm:grid-cols-[200px_1fr]">
        <Field label="Hujjat turi">
          <Select value={kind} onChange={(e) => setKind(e.target.value)}>
            {DOC_KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
                {k.required ? ' *' : ''}
              </option>
            ))}
          </Select>
        </Field>
        <div
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault()
            onFile(e.dataTransfer.files?.[0])
          }}
          onClick={() => inputRef.current?.click()}
          className="flex cursor-pointer flex-col items-center justify-center rounded-3xl border-2 border-dashed border-brand-200 bg-brand-50/40 p-6 text-center transition hover:border-brand-400 hover:bg-brand-50"
        >
          {upload.isPending ? (
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-brand-500 border-t-transparent" />
          ) : (
            <>
              <Upload className="h-6 w-6 text-brand-600" />
              <div className="mt-2 text-sm font-semibold text-ink-800">Faylni tanlang yoki shu yerga tashlang</div>
              <div className="text-xs text-ink-500">PDF, JPG, PNG · 10 MB gacha</div>
            </>
          )}
          <input ref={inputRef} type="file" accept=".pdf,image/*" className="hidden" onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = '' }} />
        </div>
      </div>

      <div className="mb-3 flex flex-wrap gap-2">
        {DOC_KINDS.filter((k) => k.required).map((k) => {
          const ok = docs.some((d) => d.kind === k.value)
          return (
            <span key={k.value} className={cx('chip', ok ? 'bg-brand-100 text-brand-700' : 'bg-amber-50 text-amber-700')}>
              {ok ? <Check className="h-3.5 w-3.5" /> : <Clock className="h-3.5 w-3.5" />} {k.label}
            </span>
          )
        })}
      </div>

      {loading && <Skeleton className="h-20" />}
      <div className="space-y-2">
        {docs.map((d) => (
          <div key={d.id} className="flex items-center gap-3 rounded-2xl border border-ink-100 p-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-sky-50 text-sky-600">
              <FileText className="h-5 w-5" />
            </div>
            <div className="min-w-0 flex-1">
              <a href={d.download_url} target="_blank" rel="noreferrer" className="block truncate font-semibold text-ink-900 hover:text-brand-700">
                {d.original_name}
              </a>
              <div className="text-xs text-ink-500">
                {DOC_KINDS.find((k) => k.value === d.kind)?.label ?? d.kind} · {(d.size / 1024).toFixed(0)} KB · {dateLong(d.created_at)}
              </div>
            </div>
            <button onClick={() => remove.mutate(d.id)} disabled={remove.isPending} className="rounded-xl p-2 text-ink-400 hover:bg-rose-50 hover:text-rose-600" aria-label="O'chirish">
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
