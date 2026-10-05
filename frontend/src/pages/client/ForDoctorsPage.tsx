import { useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { BadgeCheck, CalendarDays, FileCheck2, Send, Stethoscope, TrendingUp, UserPlus, Wallet } from 'lucide-react'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { useToast } from '@/lib/toast'
import { Button } from '@/components/ui'

export default function ForDoctorsPage() {
  const { isAuthed, user, switchRole, updateUser } = useAuth()
  const nav = useNavigate()
  const toast = useToast()

  // Shifokor roli onboarding/start bilan paydo bo'ladi (api/doctor/onboarding.py)
  const start = useMutation({
    mutationFn: async () => {
      if (user?.available_roles.includes('doctor')) return
      await api('/doctor/onboarding/start', { method: 'POST' })
      updateUser({ available_roles: [...(user?.available_roles ?? ['client']), 'doctor'] })
    },
    onSuccess: async () => {
      await switchRole('doctor')
      nav('/doctor')
    },
    onError: (e) => toast.error(e),
  })

  const go = () => (isAuthed ? start.mutate() : nav('/login?next=/for-doctors'))

  return (
    <div>
      <section className="mesh-bg -mt-[72px] pb-16 pt-[110px]">
        <div className="container-x grid items-center gap-10 lg:grid-cols-2">
          <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}>
            <span className="chip bg-brand-50 text-brand-700 ring-1 ring-brand-100">
              <Stethoscope className="h-3.5 w-3.5" /> Shifokorlar uchun
            </span>
            <h1 className="mt-5 text-4xl font-extrabold leading-tight text-ink-950 sm:text-5xl">
              Bemorlaringiz sizni <span className="text-gradient">osongina topsin</span>
            </h1>
            <p className="mt-5 text-lg text-ink-600">Onlayn jadval, avtomatik eslatmalar, to'lovlar va daromad hisobotlari — hammasi bitta kabinetda. Siz davolang, qolganini biz qilamiz.</p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Button size="lg" loading={start.isPending} onClick={go}>
                {user?.available_roles.includes('doctor') ? 'Kabinetga o‘tish' : 'Ariza topshirish'}
              </Button>
            </div>
          </motion.div>
          <div className="grid grid-cols-2 gap-4">
            {[
              { icon: CalendarDays, t: 'Moslashuvchan jadval', d: "Ish kunlari, ta'til, klinika va uy chaqiruvi" },
              { icon: Wallet, t: "Shaffof to'lovlar", d: 'Har bir qabul bo‘yicha hisobot va payout' },
              { icon: TrendingUp, t: "Ko'proq bemor", d: 'Reyting va sharhlar orqali ishonch' },
              { icon: Send, t: 'Telegram bildirishnoma', d: 'Yangi bron va bekor qilishlar haqida' },
            ].map((f, i) => (
              <motion.div key={f.t} initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 + i * 0.06 }} className="card p-5">
                <f.icon className="h-7 w-7 text-brand-600" />
                <div className="mt-3 font-bold text-ink-900">{f.t}</div>
                <div className="mt-1 text-sm text-ink-500">{f.d}</div>
              </motion.div>
            ))}
          </div>
        </div>
      </section>
      <section className="container-x py-12">
        <h2 className="text-3xl font-extrabold text-ink-950">Qanday qo'shilish mumkin?</h2>
        <div className="mt-8 grid gap-5 md:grid-cols-4">
          {[
            { icon: UserPlus, t: 'Ro‘yxatdan o‘ting', d: 'Telefon raqam orqali 1 daqiqada' },
            { icon: Stethoscope, t: "Anketani to'ldiring", d: "Mutaxassislik, tajriba, ta'lim" },
            { icon: FileCheck2, t: 'Hujjatlarni yuklang', d: 'Diplom va litsenziya' },
            { icon: BadgeCheck, t: 'Tasdiqdan o‘ting', d: 'Moderatsiya 1–2 ish kuni' },
          ].map((s, i) => (
            <div key={s.t} className="card p-6">
              <div className="flex items-center gap-3">
                <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-brand-500 text-white shadow-glow">
                  <s.icon className="h-5 w-5" />
                </span>
                <span className="text-sm font-bold text-ink-300">0{i + 1}</span>
              </div>
              <div className="mt-4 font-bold text-ink-900">{s.t}</div>
              <div className="mt-1 text-sm text-ink-500">{s.d}</div>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
