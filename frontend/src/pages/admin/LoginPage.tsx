import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft, KeyRound, ShieldCheck } from 'lucide-react'
import { ApiError } from '@/lib/api'
import { useToast } from '@/lib/toast'
import { LogoMark } from '@/components/Logo'
import { Button, Field, Input } from '@/components/ui'
import { useAdmin } from './auth'

/** Admin kirishi: 1) login + parol → 2) authenticator ilovasidagi 6 xonali TOTP kod. */
export default function AdminLoginPage() {
  const { isAuthed, login, verifyTotp } = useAdmin()
  const toast = useToast()
  const nav = useNavigate()
  const [params] = useSearchParams()
  const next = params.get('next') || '/admin'

  const [step, setStep] = useState<'password' | 'totp'>('password')
  const [loginVal, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [mfaToken, setMfaToken] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  if (isAuthed) return <Navigate to={next.startsWith('/admin') ? next : '/admin'} replace />

  const submitPassword = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const r = await login(loginVal.trim(), password)
      setMfaToken(r.mfa_token)
      setPassword('')
      setStep('totp')
    } catch (err) {
      if (err instanceof ApiError && (err.code === 'INVALID_CREDENTIALS' || err.code === 'LOGIN_LOCKED')) setError(err.message)
      else toast.error(err)
    } finally {
      setBusy(false)
    }
  }

  const submitTotp = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await verifyTotp(mfaToken, code)
      nav(next.startsWith('/admin') ? next : '/admin', { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.code === 'MFA_EXPIRED') {
        setStep('password')
        setCode('')
        setError("Kod kiritish vaqti tugadi — qaytadan kiring.")
      } else if (err instanceof ApiError && err.code === 'MFA_INVALID') {
        setCode('')
        setError(err.message)
      } else toast.error(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-ink-950 px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <LogoMark size={48} />
          <h1 className="mt-4 text-2xl font-extrabold text-white">Moderatsiya paneli</h1>
          <p className="mt-1 text-sm text-white/50">Faqat platforma xodimlari uchun</p>
        </div>
        <div className="card p-6">
          {step === 'password' ? (
            <form onSubmit={submitPassword} className="space-y-4">
              <Field label="Login">
                <Input autoFocus autoComplete="username" value={loginVal} onChange={(e) => setLogin(e.target.value)} />
              </Field>
              <Field label="Parol">
                <Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
              </Field>
              {error && <p className="text-sm font-medium text-rose-600">{error}</p>}
              <Button type="submit" block loading={busy} disabled={!loginVal.trim() || !password} icon={<KeyRound className="h-4 w-4" />}>
                Davom etish
              </Button>
            </form>
          ) : (
            <form onSubmit={submitTotp} className="space-y-4">
              <div className="flex items-start gap-3 rounded-2xl bg-brand-50 p-3.5 text-sm text-brand-900">
                <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-brand-600" />
                Authenticator ilovasidagi 6 xonali kodni kiriting.
              </div>
              <Field label="Tasdiqlash kodi">
                <Input
                  autoFocus
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                  className="text-center font-mono text-xl tracking-[0.5em]"
                />
              </Field>
              {error && <p className="text-sm font-medium text-rose-600">{error}</p>}
              <Button type="submit" block loading={busy} disabled={code.length !== 6}>
                Kirish
              </Button>
              <button
                type="button"
                onClick={() => { setStep('password'); setCode(''); setError('') }}
                className="mx-auto flex items-center gap-1.5 text-sm font-semibold text-ink-500 hover:text-ink-800"
              >
                <ArrowLeft className="h-4 w-4" /> Orqaga
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}
