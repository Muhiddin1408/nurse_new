import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { ArrowLeft, Building2, KeyRound, ShieldCheck, Smartphone, Stethoscope, User as UserIcon, UserRound } from 'lucide-react'
import { api, ApiError, asList } from '@/lib/api'
import { ROLE_HOME, ROLE_LABEL, useAuth } from '@/lib/auth'
import { phone as fmtPhone } from '@/lib/format'
import { useToast } from '@/lib/toast'
import type { AuthUser, Patient, Role } from '@/lib/types'
import { LogoMark } from '@/components/Logo'
import { Button, Field, Input, Segmented, cx } from '@/components/ui'

type Step = 'phone' | 'code' | 'profile' | 'role'

const ROLE_ICON: Record<Role, typeof UserIcon> = { client: UserIcon, doctor: Stethoscope, clinic_admin: Building2, platform_admin: ShieldCheck }
const ROLE_TEXT: Record<Role, string> = {
  client: 'Shifokorga yozilish va bronlarni boshqarish',
  doctor: 'Qabullar, jadval va daromad',
  clinic_admin: 'Klinika jadvali, shifokorlar va hisobotlar',
  platform_admin: 'Shifokorlar moderatsiyasi va nizolar',
}

/** "90 123 45 67" → "+998901234567" */
function toE164(digits: string) {
  return `+998${digits}`
}

function maskDigits(d: string) {
  const p = [d.slice(0, 2), d.slice(2, 5), d.slice(5, 7), d.slice(7, 9)].filter(Boolean)
  return p.length ? `${p[0]}${p[1] ? ' ' + p[1] : ''}${p[2] ? '-' + p[2] : ''}${p[3] ? '-' + p[3] : ''}` : ''
}

export default function LoginPage() {
  const { requestOtp, verifyOtp, switchRole, updateUser, isAuthed, user } = useAuth()
  const toast = useToast()
  const nav = useNavigate()
  const [sp] = useSearchParams()
  const next = sp.get('next')

  const [step, setStep] = useState<Step>('phone')
  const [digits, setDigits] = useState('')
  const [code, setCode] = useState('')
  const [devCode, setDevCode] = useState<string | null>(null)
  const [cooldown, setCooldown] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [authed, setAuthed] = useState<AuthUser | null>(null)

  useEffect(() => {
    if (isAuthed && user && step === 'phone') nav(next || ROLE_HOME[user.active_role], { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (cooldown <= 0) return
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000)
    return () => clearTimeout(t)
  }, [cooldown])

  const sendCode = async () => {
    setError('')
    if (digits.length !== 9) return setError("Telefon raqamni to'liq kiriting")
    setBusy(true)
    try {
      const res = await requestOtp(toE164(digits))
      setDevCode(res.debug_code ?? null)
      setStep('code')
      setCooldown(60)
      setCode('')
    } catch (e) {
      const err = e as ApiError
      if (err.status === 429) {
        const sec = Number(err.detail.match(/(\d+)\s*soniya/)?.[1])
        if (sec) {
          setCooldown(sec)
          setStep('code')
        }
      }
      setError(err.detail)
    } finally {
      setBusy(false)
    }
  }

  const finish = (u: AuthUser) => {
    const roles = u.available_roles ?? ['client']
    if (roles.length > 1 && !next) return setStep('role')
    nav(next || ROLE_HOME[u.active_role], { replace: true })
  }

  const verify = async (c = code) => {
    if (c.length !== 6) return
    setError('')
    setBusy(true)
    try {
      const res = await verifyOtp(toE164(digits), c)
      setAuthed(res.user)
      if (res.user.active_role === 'client') {
        const patients = asList<Patient>(await api('/patient/patients/').catch(() => []))
        if (res.is_new_user || patients.length === 0) return setStep('profile')
        if (!res.user.full_name) updateUser({ full_name: (patients.find((p) => /o.?zim/i.test(p.relation)) ?? patients[0]).full_name })
      }
      toast.success('Xush kelibsiz!')
      finish(res.user)
    } catch (e) {
      setError((e as ApiError).detail)
      setCode('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mesh-bg -mt-[72px] flex min-h-screen items-center justify-center px-4 pb-10 pt-[100px]">
      <div className="grid w-full max-w-5xl overflow-hidden rounded-4xl bg-white shadow-lift ring-1 ring-ink-100 lg:grid-cols-2">
        <div className="relative hidden overflow-hidden bg-gradient-to-br from-brand-500 via-brand-600 to-teal-800 p-10 text-white lg:block">
          <div className="absolute -right-20 -top-20 h-72 w-72 rounded-full bg-white/10" />
          <div className="absolute -bottom-24 -left-16 h-80 w-80 rounded-full bg-white/10" />
          <div className="relative flex h-full flex-col">
            <LogoMark size={48} />
            <h2 className="mt-10 text-3xl font-extrabold leading-tight">Sog'lig'ingiz — bir necha bosish masofada</h2>
            <p className="mt-4 text-white/80">Bir marta kiring — butun oilangiz uchun shifokorga yoziling, bronlarni kuzating va eslatmalar oling.</p>
            <ul className="mt-auto space-y-3 pt-10 text-sm">
              {['Parolsiz — faqat SMS kod', "Ma'lumotlaringiz shifrlangan", 'Telegram orqali eslatmalar'].map((t) => (
                <li key={t} className="flex items-center gap-2.5">
                  <span className="flex h-6 w-6 items-center justify-center rounded-full bg-white/20">✓</span>
                  {t}
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="p-6 sm:p-10">
          <AnimatePresence mode="wait">
            <motion.div key={step} initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} transition={{ duration: 0.2 }}>
              {step === 'phone' && (
                <form
                  onSubmit={(e) => {
                    e.preventDefault()
                    sendCode()
                  }}
                >
                  <StepIcon icon={<Smartphone className="h-6 w-6" />} />
                  <h1 className="mt-5 text-2xl font-extrabold text-ink-950">Kirish yoki ro'yxatdan o'tish</h1>
                  <p className="mt-2 text-ink-500">Telefon raqamingizni kiriting — tasdiqlash kodini SMS orqali yuboramiz.</p>
                  <label className="mt-8 block">
                    <span className="label">Telefon raqam</span>
                    <div className={cx('flex items-center rounded-2xl border bg-white transition focus-within:ring-4', error ? 'border-rose-400 focus-within:ring-rose-500/15' : 'border-ink-200 focus-within:border-brand-500 focus-within:ring-brand-500/15')}>
                      <span className="flex items-center gap-2 border-r border-ink-100 py-3.5 pl-4 pr-3 text-[17px] font-semibold text-ink-700">🇺🇿 +998</span>
                      <input
                        autoFocus
                        inputMode="numeric"
                        autoComplete="tel-national"
                        placeholder="90 123-45-67"
                        value={maskDigits(digits)}
                        onChange={(e) => setDigits(e.target.value.replace(/\D/g, '').replace(/^998/, '').slice(0, 9))}
                        className="w-full bg-transparent px-3 py-3.5 text-[17px] font-semibold tracking-wide"
                      />
                    </div>
                  </label>
                  {error && <p className="mt-2 text-sm font-medium text-rose-600">{error}</p>}
                  <Button type="submit" block size="lg" className="mt-6" loading={busy} disabled={digits.length !== 9}>
                    Kod olish
                  </Button>
                  <p className="mt-4 text-center text-xs text-ink-400">Davom etish orqali foydalanish shartlari va maxfiylik siyosatiga rozilik bildirasiz.</p>
                </form>
              )}

              {step === 'code' && (
                <div>
                  <button onClick={() => setStep('phone')} className="mb-4 inline-flex items-center gap-1.5 text-sm font-semibold text-ink-500 hover:text-ink-900">
                    <ArrowLeft className="h-4 w-4" /> Raqamni o'zgartirish
                  </button>
                  <StepIcon icon={<KeyRound className="h-6 w-6" />} />
                  <h1 className="mt-5 text-2xl font-extrabold text-ink-950">SMS kodni kiriting</h1>
                  <p className="mt-2 text-ink-500">
                    <b className="text-ink-800">{fmtPhone(toE164(digits))}</b> raqamiga 6 xonali kod yuborildi.
                  </p>
                  {devCode && (
                    <button onClick={() => { setCode(devCode); verify(devCode) }} className="mt-4 w-full rounded-2xl border border-dashed border-amber-300 bg-amber-50 px-4 py-3 text-left text-sm text-amber-800">
                      <b>Demo rejim:</b> SMS yuborilmaydi. Kod: <b className="font-mono text-base tracking-widest">{devCode}</b> — bosing, avtomatik kiritiladi.
                    </button>
                  )}
                  <OtpInput
                    value={code}
                    onChange={(v) => {
                      setCode(v)
                      if (v.length === 6) verify(v)
                    }}
                    invalid={!!error}
                    disabled={busy}
                  />
                  {error && <p className="mt-3 text-sm font-medium text-rose-600">{error}</p>}
                  <Button block size="lg" className="mt-6" loading={busy} disabled={code.length !== 6} onClick={() => verify()}>
                    Tasdiqlash
                  </Button>
                  <div className="mt-4 text-center text-sm text-ink-500">
                    {cooldown > 0 ? (
                      <>Qayta yuborish: <b className="tabular-nums text-ink-800">0:{String(cooldown).padStart(2, '0')}</b></>
                    ) : (
                      <button onClick={sendCode} className="font-semibold text-brand-600 hover:text-brand-700">
                        Kodni qayta yuborish
                      </button>
                    )}
                  </div>
                </div>
              )}

              {step === 'profile' && <ProfileStep onDone={(name) => { updateUser({ full_name: name }); toast.success("Profil yaratildi!"); if (authed) finish({ ...authed, full_name: name }) }} />}

              {step === 'role' && authed && (
                <div>
                  <StepIcon icon={<UserRound className="h-6 w-6" />} />
                  <h1 className="mt-5 text-2xl font-extrabold text-ink-950">Qaysi kabinetga kirasiz?</h1>
                  <p className="mt-2 text-ink-500">Akkauntingizda bir nechta rol bor. Keyin istalgan vaqtda almashtirishingiz mumkin.</p>
                  <div className="mt-6 space-y-3">
                    {authed.available_roles.map((r) => {
                      const Icon = ROLE_ICON[r]
                      return (
                        <button
                          key={r}
                          disabled={busy}
                          onClick={async () => {
                            setBusy(true)
                            try {
                              if (r !== authed.active_role) await switchRole(r)
                              nav(ROLE_HOME[r], { replace: true })
                            } catch (e) {
                              toast.error(e)
                            } finally {
                              setBusy(false)
                            }
                          }}
                          className="group flex w-full items-center gap-4 rounded-3xl border border-ink-100 p-4 text-left transition hover:border-brand-300 hover:bg-brand-50/50"
                        >
                          <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-ink-100 text-ink-600 transition group-hover:bg-brand-500 group-hover:text-white">
                            <Icon className="h-6 w-6" />
                          </span>
                          <span>
                            <span className="block font-bold text-ink-900">{ROLE_LABEL[r]}</span>
                            <span className="block text-sm text-ink-500">{ROLE_TEXT[r]}</span>
                          </span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </div>
  )
}

function StepIcon({ icon }: { icon: React.ReactNode }) {
  return <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-50 to-sky-50 text-brand-600 ring-1 ring-brand-100">{icon}</div>
}

function OtpInput({ value, onChange, invalid, disabled }: { value: string; onChange: (v: string) => void; invalid?: boolean; disabled?: boolean }) {
  const refs = useRef<(HTMLInputElement | null)[]>([])
  useEffect(() => {
    refs.current[Math.min(value.length, 5)]?.focus()
  }, [value.length])

  return (
    <div className="mt-6 flex justify-between gap-2" onPaste={(e) => {
      const d = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, 6)
      if (d) {
        e.preventDefault()
        onChange(d)
      }
    }}>
      {Array.from({ length: 6 }).map((_, i) => (
        <input
          key={i}
          ref={(el) => {
            refs.current[i] = el
          }}
          inputMode="numeric"
          autoComplete={i === 0 ? 'one-time-code' : 'off'}
          maxLength={1}
          disabled={disabled}
          value={value[i] ?? ''}
          onChange={(e) => {
            const ch = e.target.value.replace(/\D/g, '').slice(-1)
            if (!ch) return
            onChange((value.slice(0, i) + ch).slice(0, 6))
          }}
          onKeyDown={(e) => {
            if (e.key === 'Backspace') {
              e.preventDefault()
              onChange(value.slice(0, Math.max(0, value.length - 1)))
            }
          }}
          className={cx(
            'h-14 w-full min-w-0 rounded-2xl border-2 text-center text-2xl font-extrabold transition focus:ring-4 sm:h-16',
            invalid ? 'border-rose-300 focus:ring-rose-500/15' : value[i] ? 'border-brand-400 bg-brand-50/50 focus:ring-brand-500/15' : 'border-ink-200 focus:border-brand-500 focus:ring-brand-500/15',
          )}
        />
      ))}
    </div>
  )
}

function ProfileStep({ onDone }: { onDone: (name: string) => void }) {
  const toast = useToast()
  const [name, setName] = useState('')
  const [birth, setBirth] = useState('')
  const [gender, setGender] = useState<'male' | 'female'>('male')
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    const errs: Record<string, string> = {}
    if (name.trim().split(/\s+/).length < 2) errs.full_name = 'Ism va familiyani kiriting'
    if (!birth) errs.birth_date = "Tug'ilgan sanani kiriting"
    setErrors(errs)
    if (Object.keys(errs).length) return
    setBusy(true)
    try {
      await api('/patient/patients/', { method: 'POST', body: { full_name: name.trim(), birth_date: birth, gender, relation: "O'zim" } })
      onDone(name.trim())
    } catch (err) {
      const ae = err as ApiError
      setErrors(ae.fields)
      toast.error(ae)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit}>
      <StepIcon icon={<UserRound className="h-6 w-6" />} />
      <h1 className="mt-5 text-2xl font-extrabold text-ink-950">Tanishib olaylik</h1>
      <p className="mt-2 text-ink-500">Bu ma'lumotlar shifokorga qabulga tayyorlanish uchun kerak. Oila a'zolarini keyin qo'shasiz.</p>
      <div className="mt-6 space-y-4">
        <Field label="Ism va familiya" error={errors.full_name}>
          <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Masalan: Aziz Karimov" invalid={!!errors.full_name} />
        </Field>
        <Field label="Tug'ilgan sana" error={errors.birth_date}>
          <Input type="date" value={birth} max={new Date().toISOString().slice(0, 10)} onChange={(e) => setBirth(e.target.value)} invalid={!!errors.birth_date} />
        </Field>
        <div>
          <span className="label">Jinsi</span>
          <Segmented className="w-full" value={gender} onChange={setGender} options={[{ value: 'male', label: 'Erkak' }, { value: 'female', label: 'Ayol' }]} />
        </div>
      </div>
      <Button type="submit" block size="lg" className="mt-6" loading={busy}>
        Davom etish
      </Button>
    </form>
  )
}
