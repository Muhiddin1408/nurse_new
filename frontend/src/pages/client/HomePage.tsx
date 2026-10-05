import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import {
  ArrowRight, BadgeCheck, Bell, Building2, CalendarCheck2, CalendarDays, ChevronDown, CircleCheckBig, Clock, CreditCard, House, MapPin,
  Search, ShieldCheck, Star, Stethoscope, Users,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useClinics, useSpecializations } from '@/lib/queries'
import type { DoctorList } from '@/lib/types'
import { DoctorCard, DoctorCardSkeleton, specIcon, specTone } from '@/components/client'
import { Avatar, Button, Skeleton, Stars, cx } from '@/components/ui'

const fade = { initial: { opacity: 0, y: 24 }, whileInView: { opacity: 1, y: 0 }, viewport: { once: true, margin: '-60px' }, transition: { duration: 0.5 } }

export default function HomePage() {
  return (
    <>
      <Hero />
      <Specializations />
      <TopDoctors />
      <HowItWorks />
      <HomeVisitBanner />
      <Benefits />
      <Clinics />
      <Testimonials />
      <Faq />
      <DoctorCta />
    </>
  )
}

function Hero() {
  const nav = useNavigate()
  const specs = useSpecializations()
  const [q, setQ] = useState('')
  const [spec, setSpec] = useState('')

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const p = new URLSearchParams()
    if (q.trim()) p.set('q', q.trim())
    if (spec) p.set('specialization', spec)
    nav(`/doctors${p.toString() ? '?' + p : ''}`)
  }

  return (
    <section className="mesh-bg relative -mt-[72px] overflow-hidden pt-[72px]">
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_right,rgba(100,117,150,.06)_1px,transparent_1px),linear-gradient(to_bottom,rgba(100,117,150,.06)_1px,transparent_1px)] bg-[size:56px_56px] [mask-image:radial-gradient(ellipse_at_top,black_30%,transparent_70%)]" />
      <div className="container-x relative grid items-center gap-12 pb-20 pt-10 lg:grid-cols-[1.1fr_.9fr] lg:pb-28 lg:pt-16">
        <div>
          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="inline-flex items-center gap-2 rounded-full bg-white/80 px-3 py-1.5 text-sm font-semibold text-brand-700 shadow-soft ring-1 ring-brand-100 backdrop-blur">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-400 opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-brand-500" />
            </span>
            Bugun ham bo'sh vaqtlar bor
          </motion.div>
          <motion.h1
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.05 }}
            className="mt-5 text-[2.5rem] font-extrabold leading-[1.08] text-ink-950 sm:text-5xl lg:text-[3.6rem]"
          >
            Ishonchli shifokorga <br className="hidden sm:block" />
            <span className="text-gradient">2 daqiqada</span> yoziling
          </motion.h1>
          <motion.p initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }} className="mt-5 max-w-xl text-lg leading-relaxed text-ink-600">
            Tasdiqlangan shifokorlar, real bo'sh vaqtlar va onlayn to'lov. Klinikaga boring yoki shifokorni uyingizga chaqiring — navbatsiz va qo'ng'iroqsiz.
          </motion.p>

          <motion.form
            onSubmit={submit}
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.15 }}
            className="mt-8 flex flex-col gap-2 rounded-3xl bg-white p-2 shadow-lift ring-1 ring-ink-100 sm:flex-row sm:items-center"
          >
            <div className="flex flex-1 items-center gap-3 px-4 py-2">
              <Search className="h-5 w-5 shrink-0 text-ink-400" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Shifokor ismi yoki mutaxassislik" className="w-full bg-transparent py-1.5 text-[15px] placeholder:text-ink-400" />
            </div>
            <div className="hidden h-8 w-px bg-ink-100 sm:block" />
            <div className="flex items-center gap-3 px-4 py-2 sm:w-56">
              <Stethoscope className="h-5 w-5 shrink-0 text-ink-400" />
              <select value={spec} onChange={(e) => setSpec(e.target.value)} className="w-full bg-transparent py-1.5 text-[15px] text-ink-700">
                <option value="">Barcha yo'nalishlar</option>
                {specs.data?.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>
            <Button type="submit" size="lg" className="sm:px-8">
              Topish
            </Button>
          </motion.form>

          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.25 }} className="mt-5 flex flex-wrap items-center gap-2 text-sm">
            <span className="text-ink-500">Ommabop:</span>
            {specs.data?.slice(0, 5).map((s) => (
              <Link key={s.id} to={`/doctors?specialization=${s.id}`} className="rounded-full bg-white/70 px-3 py-1 font-semibold text-ink-700 ring-1 ring-ink-200/70 transition hover:bg-white hover:text-brand-700 hover:ring-brand-200">
                {s.name}
              </Link>
            ))}
          </motion.div>

          <div className="mt-10 grid max-w-lg grid-cols-3 gap-4">
            {[
              { v: '4.8', l: "O'rtacha reyting", icon: Star },
              { v: '2 daq', l: 'Bron qilish vaqti', icon: Clock },
              { v: '24/7', l: 'Onlayn yozilish', icon: CalendarCheck2 },
            ].map((s) => (
              <div key={s.l}>
                <div className="flex items-center gap-1.5 text-2xl font-extrabold text-ink-900">
                  {s.v}
                  <s.icon className="h-4 w-4 text-brand-500" />
                </div>
                <div className="text-sm text-ink-500">{s.l}</div>
              </div>
            ))}
          </div>
        </div>
        <HeroVisual />
      </div>
    </section>
  )
}

function HeroVisual() {
  return (
    <div className="relative mx-auto hidden h-[520px] w-full max-w-md lg:block">
      <div className="absolute inset-8 rounded-[3rem] bg-gradient-to-br from-brand-400 via-brand-500 to-teal-700 shadow-glow" />
      <div className="absolute inset-8 overflow-hidden rounded-[3rem]">
        <div className="absolute -right-16 -top-16 h-64 w-64 rounded-full bg-white/10" />
        <div className="absolute -bottom-20 -left-10 h-72 w-72 rounded-full bg-white/10" />
        <svg className="absolute bottom-10 left-1/2 w-[85%] -translate-x-1/2 text-white/25" viewBox="0 0 300 80" fill="none" stroke="currentColor" strokeWidth="3">
          <path d="M0 40h70l12-28 18 56 16-44 10 16h54l10-20 14 40 12-20h84" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>

      <motion.div initial={{ opacity: 0, x: -30 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.3 }} className="absolute left-0 top-16 w-72 animate-float rounded-3xl bg-white p-4 shadow-lift">
        <div className="flex items-center gap-3">
          <Avatar name="Aziza Karimova" size={48} />
          <div>
            <div className="font-bold text-ink-900">Dr. Aziza Karimova</div>
            <div className="text-sm text-brand-700">Terapevt · 12 yil</div>
          </div>
        </div>
        <div className="mt-3 flex items-center gap-2">
          <Stars value={5} size={14} />
          <span className="text-sm font-semibold text-ink-600">4.9</span>
        </div>
        <div className="mt-3 grid grid-cols-3 gap-1.5">
          {['09:00', '10:30', '14:00'].map((t, i) => (
            <span key={t} className={cx('rounded-xl py-1.5 text-center text-sm font-bold', i === 1 ? 'bg-brand-500 text-white' : 'bg-ink-50 text-ink-700')}>
              {t}
            </span>
          ))}
        </div>
      </motion.div>

      <motion.div initial={{ opacity: 0, x: 30 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.45 }} className="absolute bottom-24 right-0 w-64 rounded-3xl bg-white p-4 shadow-lift [animation-delay:1.5s] animate-float">
        <div className="flex items-center gap-3">
          <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-brand-50 text-brand-600">
            <CircleCheckBig className="h-6 w-6" />
          </span>
          <div>
            <div className="font-bold text-ink-900">Bron tasdiqlandi!</div>
            <div className="text-sm text-ink-500">Ertaga, 10:30</div>
          </div>
        </div>
      </motion.div>

      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.6 }} className="absolute right-6 top-6 flex items-center gap-2 rounded-2xl bg-white/95 px-3 py-2 shadow-lift">
        <ShieldCheck className="h-5 w-5 text-brand-600" />
        <span className="text-sm font-bold text-ink-800">Litsenziyasi tekshirilgan</span>
      </motion.div>

      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.75 }} className="absolute bottom-4 left-10 flex items-center gap-2 rounded-2xl bg-ink-900 px-4 py-2.5 text-white shadow-lift">
        <House className="h-5 w-5 text-brand-300" />
        <span className="text-sm font-semibold">Uyga chaqiruv — 60 daqiqada</span>
      </motion.div>
    </div>
  )
}

function SectionTitle({ eyebrow, title, text, action }: { eyebrow: string; title: string; text?: string; action?: React.ReactNode }) {
  return (
    <motion.div {...fade} className="mb-10 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="max-w-2xl">
        <div className="text-sm font-bold uppercase tracking-[0.16em] text-brand-600">{eyebrow}</div>
        <h2 className="mt-2 text-3xl font-extrabold text-ink-950 sm:text-4xl">{title}</h2>
        {text && <p className="mt-3 text-lg text-ink-500">{text}</p>}
      </div>
      {action}
    </motion.div>
  )
}

function Specializations() {
  const { data, isLoading } = useSpecializations()
  return (
    <section className="container-x py-16 sm:py-20">
      <SectionTitle eyebrow="Yo'nalishlar" title="Qaysi mutaxassis kerak?" text="Yo'nalishni tanlang — shu sohadagi eng yaxshi shifokorlarni ko'rsatamiz." />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {isLoading && Array.from({ length: 10 }).map((_, i) => <Skeleton key={i} className="h-32 rounded-3xl" />)}
        {data?.map((s, i) => {
          const Icon = specIcon(s.slug)
          return (
            <motion.div key={s.id} {...fade} transition={{ delay: i * 0.03 }}>
              <Link to={`/doctors?specialization=${s.id}`} className="card group flex h-full flex-col gap-4 p-5 transition hover:-translate-y-1 hover:shadow-lift">
                <span className={cx('flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br transition group-hover:scale-110', specTone(i))}>
                  <Icon className="h-6 w-6" />
                </span>
                <div className="flex items-end justify-between gap-2">
                  <span className="font-bold text-ink-900">{s.name}</span>
                  <ArrowRight className="h-4 w-4 -translate-x-1 text-ink-300 opacity-0 transition group-hover:translate-x-0 group-hover:text-brand-600 group-hover:opacity-100" />
                </div>
              </Link>
            </motion.div>
          )
        })}
      </div>
    </section>
  )
}

function TopDoctors() {
  const { data, isLoading } = useQuery({
    queryKey: ['doctors', 'top'],
    queryFn: () => api<{ results: DoctorList[] }>('/catalog/search', { auth: false, query: { sort: 'rating', limit: 8 } }).then((r) => r.results),
  })
  return (
    <section className="bg-white py-16 sm:py-20">
      <div className="container-x">
        <SectionTitle
          eyebrow="Eng yaxshilar"
          title="Bemorlar tanlagan shifokorlar"
          text="Reyting haqiqiy qabullardan keyin yozilgan sharhlar asosida hisoblanadi."
          action={
            <Link to="/doctors">
              <Button variant="outline" icon={<ArrowRight className="h-4 w-4" />} className="flex-row-reverse">
                Barcha shifokorlar
              </Button>
            </Link>
          }
        />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {isLoading && Array.from({ length: 4 }).map((_, i) => <DoctorCardSkeleton key={i} />)}
          {data?.map((d, i) => <DoctorCard key={d.id} d={d} index={i} />)}
        </div>
      </div>
    </section>
  )
}

function HowItWorks() {
  const steps = [
    { icon: Search, t: 'Shifokorni toping', d: "Yo'nalish, reyting yoki ism bo'yicha qidiring. Har bir shifokorning tajribasi va sharhlari ochiq." },
    { icon: CalendarDays, t: 'Vaqtni tanlang', d: "Faqat haqiqatan bo'sh vaqtlar ko'rsatiladi. Klinikada yoki uyda — o'zingiz tanlaysiz." },
    { icon: CreditCard, t: "Bron qiling", d: "Payme yoki Click orqali xavfsiz to'lov, yoki klinikada to'lash. Chegirmalar avtomatik." },
    { icon: Bell, t: 'Eslatma oling', d: "SMS va Telegram orqali tasdiq va eslatma keladi. Qabuldan keyin sharh qoldiring." },
  ]
  return (
    <section id="how" className="container-x scroll-mt-24 py-16 sm:py-24">
      <SectionTitle eyebrow="Qanday ishlaydi" title="To'rt qadam — va siz qabuldasiz" />
      <div className="relative grid gap-5 md:grid-cols-2 lg:grid-cols-4">
        <div className="absolute left-0 right-0 top-10 hidden h-0.5 bg-gradient-to-r from-brand-100 via-brand-300 to-brand-100 lg:block" />
        {steps.map((s, i) => (
          <motion.div key={s.t} {...fade} transition={{ delay: i * 0.08 }} className="relative">
            <div className="relative z-10 mb-5 flex h-20 w-20 items-center justify-center rounded-3xl bg-white shadow-lift ring-1 ring-ink-100">
              <s.icon className="h-8 w-8 text-brand-600" />
              <span className="absolute -right-2 -top-2 flex h-7 w-7 items-center justify-center rounded-full bg-ink-900 text-xs font-bold text-white">{i + 1}</span>
            </div>
            <h3 className="text-lg font-bold text-ink-900">{s.t}</h3>
            <p className="mt-2 leading-relaxed text-ink-500">{s.d}</p>
          </motion.div>
        ))}
      </div>
    </section>
  )
}

function HomeVisitBanner() {
  return (
    <section className="container-x">
      <motion.div {...fade} className="relative overflow-hidden rounded-4xl bg-ink-950 px-6 py-12 sm:px-12 lg:py-16">
        <div className="absolute -right-24 -top-24 h-96 w-96 rounded-full bg-brand-500/30 blur-3xl" />
        <div className="absolute -bottom-32 left-1/3 h-80 w-80 rounded-full bg-sky-500/20 blur-3xl" />
        <div className="relative grid items-center gap-10 lg:grid-cols-2">
          <div>
            <span className="chip bg-white/10 text-brand-300">
              <House className="h-3.5 w-3.5" /> Uyga chaqiruv
            </span>
            <h2 className="mt-4 text-3xl font-extrabold text-white sm:text-4xl">Shifokor yoki hamshira — to'g'ridan-to'g'ri uyingizga</h2>
            <p className="mt-4 max-w-lg text-lg text-ink-300">
              Bola kasal bo'lsa, keksa ota-onangiz yurolmasa yoki shunchaki vaqtingiz bo'lmasa. Manzilni belgilang — qolganini biz hal qilamiz.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link to="/doctors?home=1">
                <Button size="lg">Uyga shifokor chaqirish</Button>
              </Link>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            {[
              { icon: Clock, t: 'Tez yetib kelish', d: 'Belgilangan vaqtda, kechikishsiz' },
              { icon: ShieldCheck, t: 'Tekshirilgan', d: 'Har bir shifokor litsenziyali' },
              { icon: Users, t: 'Butun oila uchun', d: "Bolalar va keksalar uchun profil" },
              { icon: CreditCard, t: "Onlayn to'lov", d: 'Payme va Click orqali' },
            ].map((f) => (
              <div key={f.t} className="rounded-3xl bg-white/5 p-5 ring-1 ring-white/10 backdrop-blur">
                <f.icon className="h-6 w-6 text-brand-300" />
                <div className="mt-3 font-bold text-white">{f.t}</div>
                <div className="mt-1 text-sm text-ink-400">{f.d}</div>
              </div>
            ))}
          </div>
        </div>
      </motion.div>
    </section>
  )
}

function Benefits() {
  const items = [
    { icon: BadgeCheck, t: 'Faqat tasdiqlangan shifokorlar', d: "Diplom va litsenziyasi moderatorlar tomonidan qo'lda tekshiriladi." },
    { icon: CalendarCheck2, t: "Real bo'sh vaqtlar", d: "Jadval shifokor tomonidan yuritiladi — «band ekan» degan qo'ng'iroqlar yo'q." },
    { icon: CreditCard, t: "Shaffof narx va qaytarish", d: "Narx oldindan ko'rinadi. Bekor qilsangiz — qaytariladigan summa ham oldindan aytiladi." },
    { icon: Users, t: 'Oila profillari', d: "Bitta akkauntdan farzandlaringiz va ota-onangizni ham yozdiring." },
    { icon: Bell, t: 'SMS va Telegram eslatma', d: "Qabuldan oldin eslatamiz, shifokor kechiksa xabar beramiz." },
    { icon: Star, t: 'Halol sharhlar', d: "Sharhni faqat qabulda bo'lgan bemor yozadi — soxta reyting yo'q." },
  ]
  return (
    <section className="container-x py-16 sm:py-24">
      <SectionTitle eyebrow="Nega Turon Clinic" title="Sog'lig'ingiz bilan bog'liq hamma narsa — bitta joyda" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {items.map((it, i) => (
          <motion.div key={it.t} {...fade} transition={{ delay: i * 0.05 }} className="card p-6">
            <span className={cx('flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br', specTone(i))}>
              <it.icon className="h-6 w-6" />
            </span>
            <h3 className="mt-4 text-lg font-bold text-ink-900">{it.t}</h3>
            <p className="mt-2 leading-relaxed text-ink-500">{it.d}</p>
          </motion.div>
        ))}
      </div>
    </section>
  )
}

function Clinics() {
  const { data, isLoading } = useClinics()
  if (!isLoading && !data?.length) return null
  return (
    <section className="bg-white py-16 sm:py-20">
      <div className="container-x">
        <SectionTitle
          eyebrow="Filiallar"
          title="Klinikalarimiz"
          action={
            <Link to="/clinics">
              <Button variant="outline">Barchasi</Button>
            </Link>
          }
        />
        <div className="grid gap-4 md:grid-cols-3">
          {isLoading && Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-48 rounded-3xl" />)}
          {data?.slice(0, 3).map((c, i) => (
            <motion.div key={c.id} {...fade} transition={{ delay: i * 0.06 }}>
              <Link to={`/doctors?clinic=${c.id}`} className="group block overflow-hidden rounded-3xl ring-1 ring-ink-100 transition hover:shadow-lift">
                <div className={cx('relative h-32 bg-gradient-to-br', ['from-brand-400 to-teal-700', 'from-sky-400 to-indigo-600', 'from-violet-400 to-fuchsia-600'][i % 3])}>
                  <Building2 className="absolute bottom-4 right-4 h-16 w-16 text-white/20" />
                  {Number(c.rating) > 0 && (
                    <span className="absolute left-4 top-4 chip bg-white/90 text-ink-800">
                      <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-400" /> {Number(c.rating).toFixed(1)}
                    </span>
                  )}
                </div>
                <div className="bg-white p-5">
                  <h3 className="font-bold text-ink-900 group-hover:text-brand-700">{c.name}</h3>
                  <p className="mt-1 flex items-start gap-1.5 text-sm text-ink-500">
                    <MapPin className="mt-0.5 h-4 w-4 shrink-0" /> {c.city}, {c.street}
                  </p>
                </div>
              </Link>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  )
}

function Testimonials() {
  const { data: doctors } = useQuery({
    queryKey: ['doctors', 'top'],
    queryFn: () => api<{ results: DoctorList[] }>('/catalog/search', { auth: false, query: { sort: 'rating', limit: 8 } }).then((r) => r.results),
  })
  const ids = doctors?.slice(0, 3).map((d) => d.id) ?? []
  const { data: reviews } = useQuery({
    queryKey: ['home-reviews', ids],
    enabled: ids.length > 0,
    queryFn: async () => {
      const all = await Promise.all(
        ids.map((id, i) =>
          api<{ id: string; rating: number; comment: string; author: string | null }[]>(`/catalog/doctors/${id}/reviews`, { auth: false, query: { limit: 2 } }).then((rs) =>
            rs.map((r) => ({ ...r, doctor: doctors![i].full_name })),
          ),
        ),
      )
      return all.flat().filter((r) => r.comment).slice(0, 3)
    },
  })
  if (!reviews?.length) return null
  return (
    <section className="container-x py-16 sm:py-24">
      <SectionTitle eyebrow="Sharhlar" title="Bemorlarimiz nima deydi" />
      <div className="grid gap-4 md:grid-cols-3">
        {reviews.map((r, i) => (
          <motion.figure key={r.id} {...fade} transition={{ delay: i * 0.08 }} className="card flex flex-col p-6">
            <Stars value={r.rating} />
            <blockquote className="mt-4 flex-1 text-[17px] leading-relaxed text-ink-700">“{r.comment}”</blockquote>
            <figcaption className="mt-6 flex items-center gap-3">
              <Avatar name={r.author || 'Bemor'} size={40} className="rounded-xl" />
              <div>
                <div className="font-bold text-ink-900">{r.author || 'Anonim bemor'}</div>
                <div className="text-sm text-ink-500">{r.doctor} qabulida</div>
              </div>
            </figcaption>
          </motion.figure>
        ))}
      </div>
    </section>
  )
}

function Faq() {
  const items = [
    { q: "To'lovni qanday amalga oshiraman?", a: "Shifokor sozlamasiga qarab: onlayn (Payme yoki Click), qisman oldindan to'lov yoki to'liq klinikada. Uy chaqiruvi har doim oldindan to'lanadi. Bron qilishda qaysi usul ekanini ko'rasiz." },
    { q: 'Bronni bekor qilsam pul qaytariladimi?', a: "Ha. Bekor qilishdan oldin qancha qaytarilishi va jarima bor-yo'qligini aniq ko'rsatamiz. Qabulga qancha erta bekor qilsangiz, shuncha ko'p qaytariladi." },
    { q: 'Farzandim yoki ota-onam uchun yozila olamanmi?', a: "Albatta. Profilda bemorlar ro'yxatiga oila a'zolaringizni qo'shing va bron qilishda kerakli bemorni tanlang." },
    { q: "Bo'sh vaqt yo'q bo'lsa nima qilaman?", a: "Shifokor sahifasida «Bo'shasa xabar bering» tugmasini bosing — joy ochilishi bilan SMS yoki Telegram orqali xabar beramiz." },
    { q: 'Shifokorlar tekshiriladimi?', a: "Har bir shifokor diplom va litsenziya hujjatlarini yuklaydi, moderatorlarimiz ularni qo'lda tekshiradi. Litsenziya muddati ham nazorat qilinadi." },
  ]
  const [open, setOpen] = useState<number | null>(0)
  return (
    <section className="container-x pb-16 sm:pb-24">
      <div className="grid gap-10 lg:grid-cols-[.8fr_1.2fr]">
        <SectionTitle eyebrow="Savollar" title="Ko'p so'raladigan savollar" text="Javob topmadingizmi? Telegram botimizga yozing — tez javob beramiz." />
        <div className="space-y-3">
          {items.map((it, i) => (
            <div key={it.q} className="card overflow-hidden">
              <button onClick={() => setOpen(open === i ? null : i)} className="flex w-full items-center justify-between gap-4 p-5 text-left">
                <span className="font-bold text-ink-900">{it.q}</span>
                <ChevronDown className={cx('h-5 w-5 shrink-0 text-ink-400 transition', open === i && 'rotate-180 text-brand-600')} />
              </button>
              <motion.div initial={false} animate={{ height: open === i ? 'auto' : 0 }} className="overflow-hidden">
                <p className="px-5 pb-5 leading-relaxed text-ink-600">{it.a}</p>
              </motion.div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

function DoctorCta() {
  return (
    <section className="container-x">
      <motion.div {...fade} className="relative overflow-hidden rounded-4xl bg-gradient-to-br from-brand-500 to-teal-700 px-6 py-12 text-center sm:px-12">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(255,255,255,.18),transparent_40%)]" />
        <div className="relative mx-auto max-w-2xl">
          <Stethoscope className="mx-auto h-10 w-10 text-white/80" />
          <h2 className="mt-4 text-3xl font-extrabold text-white sm:text-4xl">Siz shifokormisiz?</h2>
          <p className="mt-3 text-lg text-white/80">Turon Clinic'ga qo'shiling: onlayn jadval, yangi bemorlar va har hafta shaffof to'lovlar.</p>
          <Link to="/for-doctors" className="mt-8 inline-block">
            <Button size="lg" variant="dark">
              Hamkor bo'lish
            </Button>
          </Link>
        </div>
      </motion.div>
    </section>
  )
}
