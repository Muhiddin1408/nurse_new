import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, Globe, LogOut, MessageSquare, Send, ShieldAlert, Smartphone, Trash2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { phone } from '@/lib/format'
import { useToast } from '@/lib/toast'
import type { Preferences } from '@/lib/types'
import { Badge, Button, ConfirmDialog, Input, PageHeader, Segmented, Skeleton, Toggle } from '@/components/ui'

export default function SettingsPage() {
  const { user, logout } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const nav = useNavigate()
  const [confirmAll, setConfirmAll] = useState(false)
  const [deleteStep, setDeleteStep] = useState<0 | 1 | 2>(0)
  const [typed, setTyped] = useState('')

  const prefs = useQuery({ queryKey: ['preferences'], queryFn: () => api<Preferences>('/accounts/me/preferences') })
  const save = useMutation({
    mutationFn: (patch: Partial<Preferences>) => api<Preferences>('/accounts/me/preferences', { method: 'PATCH', body: patch }),
    onMutate: async (patch) => {
      // Optimistik: toggle darhol o'zgaradi, xato bo'lsa qaytadi
      const prev = qc.getQueryData<Preferences>(['preferences'])
      if (prev) qc.setQueryData(['preferences'], { ...prev, ...patch })
      return { prev }
    },
    onError: (e, _p, ctx) => {
      if (ctx?.prev) qc.setQueryData(['preferences'], ctx.prev)
      toast.error(e)
    },
    onSuccess: (p) => qc.setQueryData(['preferences'], p),
  })

  const tg = useMutation({
    mutationFn: () => api<{ url: string; linked: boolean }>('/accounts/me/telegram/link'),
    onSuccess: (r) => window.open(r.url, '_blank', 'noopener'),
    onError: (e) => toast.error(e),
  })

  const del = useMutation({
    mutationFn: () => api('/accounts/me/delete', { method: 'POST' }),
    onSuccess: async () => {
      toast.success("Akkaunt o'chirildi")
      await logout()
      nav('/')
    },
    onError: (e) => {
      setDeleteStep(0)
      toast.error((e as ApiError).status === 409 ? 'Avval faol bronlarni yakunlang yoki bekor qiling' : e)
    },
  })

  const p = prefs.data

  return (
    <div className="space-y-6">
      <PageHeader title="Sozlamalar" subtitle={`${user?.full_name || ''} · ${phone(user?.phone)}`} />

      <section className="card p-6">
        <h2 className="mb-4 flex items-center gap-2 font-bold text-ink-900"><Bell className="h-5 w-5 text-brand-600" /> Bildirishnomalar</h2>
        {!p ? (
          <div className="space-y-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-12 rounded-2xl" />)}</div>
        ) : (
          <div className="divide-y divide-ink-100">
            <Row icon={<MessageSquare className="h-5 w-5" />} title="SMS xabarlar" text="Bron tasdig'i va eslatmalar">
              <Toggle checked={p.sms_enabled} onChange={(v) => save.mutate({ sms_enabled: v })} />
            </Row>
            <Row icon={<Smartphone className="h-5 w-5" />} title="Push bildirishnomalar" text="Mobil ilovada">
              <Toggle checked={p.push_enabled} onChange={(v) => save.mutate({ push_enabled: v })} />
            </Row>
            <Row icon={<Send className="h-5 w-5" />} title="Telegram" text={p.telegram_linked ? 'Ulangan ✓' : 'Eslatmalarni Telegramda oling'}>
              <div className="flex items-center gap-3">
                {!p.telegram_linked && <Button size="sm" variant="secondary" loading={tg.isPending} onClick={() => tg.mutate()}>Ulash</Button>}
                <Toggle checked={p.telegram_enabled} disabled={!p.telegram_linked} onChange={(v) => save.mutate({ telegram_enabled: v })} />
              </div>
            </Row>
            <Row icon={<Bell className="h-5 w-5" />} title="Aksiya va yangiliklar" text="Chegirmalar haqida kamdan-kam xabar">
              <Toggle checked={p.marketing_opt_in} onChange={(v) => save.mutate({ marketing_opt_in: v })} />
            </Row>
          </div>
        )}
      </section>

      <section className="card p-6">
        <h2 className="mb-4 flex items-center gap-2 font-bold text-ink-900"><Globe className="h-5 w-5 text-brand-600" /> Til</h2>
        {p && (
          <Segmented value={p.language} onChange={(v) => save.mutate({ language: v })} options={[{ value: 'uz', label: "O'zbekcha" }, { value: 'ru', label: 'Русский' }]} />
        )}
        <p className="mt-2 text-sm text-ink-500">SMS va bildirishnomalar shu tilda yuboriladi.</p>
      </section>

      <section className="card p-6">
        <h2 className="mb-4 flex items-center gap-2 font-bold text-ink-900"><ShieldAlert className="h-5 w-5 text-brand-600" /> Xavfsizlik</h2>
        <div className="flex flex-wrap gap-3">
          <Button variant="outline" icon={<LogOut className="h-4 w-4" />} onClick={async () => { await logout(); nav('/') }}>Chiqish</Button>
          <Button variant="outline" onClick={() => setConfirmAll(true)}>Barcha qurilmalardan chiqish</Button>
        </div>
        <p className="mt-3 text-sm text-ink-500">Telefoningiz yo'qolgan bo'lsa — barcha qurilmalardan chiqing.</p>
      </section>

      <section className="card border border-rose-100 p-6">
        <h2 className="flex items-center gap-2 font-bold text-rose-700"><Trash2 className="h-5 w-5" /> Akkauntni o'chirish</h2>
        <p className="mt-2 text-sm text-ink-500">Shaxsiy ma'lumotlaringiz o'chiriladi. Faol bronlar bo'lsa, avval ularni yakunlang. <Badge tone="red">Qaytarib bo'lmaydi</Badge></p>
        <Button variant="danger" className="mt-4" onClick={() => setDeleteStep(1)}>Akkauntni o'chirish</Button>
      </section>

      <ConfirmDialog
        open={confirmAll}
        onClose={() => setConfirmAll(false)}
        onConfirm={async () => { await logout(true); nav('/') }}
        title="Barcha qurilmalardan chiqasizmi?"
        text="Barcha qurilmalardagi sessiyalar yopiladi, qayta SMS kod bilan kirish kerak bo'ladi."
        confirmText="Chiqish"
      />
      <ConfirmDialog
        open={deleteStep === 1}
        onClose={() => setDeleteStep(0)}
        onConfirm={() => { setTyped(''); setDeleteStep(2) }}
        danger
        title="Rostdan ham o'chirmoqchimisiz?"
        text="Bemorlar, manzillar va sevimlilar o'chadi. Bronlar tarixi qonun talabiga ko'ra anonim holda saqlanadi."
        confirmText="Davom etish"
      />
      <ConfirmDialog
        open={deleteStep === 2}
        onClose={() => setDeleteStep(0)}
        onConfirm={() => typed.trim().toUpperCase() === "O'CHIRISH" && del.mutate()}
        loading={del.isPending}
        danger
        title="Oxirgi tasdiq"
        confirmText="O'chirish"
      >
        <p className="text-sm text-ink-600">Tasdiqlash uchun <b>O'CHIRISH</b> deb yozing:</p>
        <Input className="mt-3" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="O'CHIRISH" />
      </ConfirmDialog>
    </div>
  )
}

function Row({ icon, title, text, children }: { icon: React.ReactNode; title: string; text: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-4 py-3.5">
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-ink-50 text-ink-500">{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="font-semibold text-ink-900">{title}</div>
        <div className="text-sm text-ink-500">{text}</div>
      </div>
      {children}
    </div>
  )
}
