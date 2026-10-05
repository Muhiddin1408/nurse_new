import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowRight, CalendarCheck2, CalendarPlus, Heart, House, MapPin, Search, Users } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { relativeDateTime } from '@/lib/format'
import { useAddresses, useFavorites, usePatients } from '@/lib/queries'
import { DoctorCard } from '@/components/client'
import { Button, Skeleton } from '@/components/ui'
import { ACTIVE_STATUSES, BookingCard, useMyBookings } from './shared'

function greeting() {
  const h = new Date().getHours()
  if (h < 5) return 'Xayrli tun'
  if (h < 12) return 'Xayrli tong'
  if (h < 18) return 'Xayrli kun'
  return 'Xayrli kech'
}

export default function Overview() {
  const { user } = useAuth()
  const active = useMyBookings(ACTIVE_STATUSES, 5)
  const patients = usePatients()
  const addresses = useAddresses()
  const favs = useFavorites()
  const upcoming = active.data?.pages[0]?.results ?? []
  const next = [...upcoming].filter((b) => b.start_at).sort((a, b) => (a.start_at! < b.start_at! ? -1 : 1))[0]

  return (
    <div className="space-y-8">
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="relative overflow-hidden rounded-4xl bg-gradient-to-br from-ink-900 via-ink-900 to-brand-900 p-6 text-white sm:p-8">
        <div className="absolute -right-16 -top-16 h-64 w-64 rounded-full bg-brand-500/30 blur-3xl" />
        <div className="relative">
          <div className="text-white/60">{greeting()},</div>
          <h1 className="text-2xl font-extrabold sm:text-3xl">{user?.full_name?.split(' ')[0] || 'xush kelibsiz'} 👋</h1>
          {active.isLoading ? (
            <Skeleton className="mt-6 h-16 max-w-md bg-white/10" />
          ) : next ? (
            <Link to={`/account/bookings/${next.id}`} className="mt-6 flex max-w-lg items-center gap-4 rounded-3xl bg-white/10 p-4 ring-1 ring-white/10 backdrop-blur transition hover:bg-white/15">
              <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-500">
                <CalendarCheck2 className="h-6 w-6" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-sm text-white/60">Keyingi qabul</div>
                <div className="truncate font-bold">
                  {relativeDateTime(next.start_at)} · {next.doctor_name}
                </div>
              </div>
              <ArrowRight className="h-5 w-5 text-white/60" />
            </Link>
          ) : (
            <div className="mt-6 flex flex-wrap items-center gap-3">
              <p className="text-white/70">Rejalashtirilgan qabullar yo'q.</p>
              <Link to="/doctors">
                <Button size="sm" icon={<CalendarPlus className="h-4 w-4" />}>
                  Qabulga yozilish
                </Button>
              </Link>
            </div>
          )}
        </div>
      </motion.div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          { to: '/doctors', icon: Search, label: 'Shifokor topish', tone: 'from-brand-50 to-brand-100 text-brand-700' },
          { to: '/doctors?home=1', icon: House, label: 'Uyga chaqirish', tone: 'from-sky-50 to-sky-100 text-sky-700' },
          { to: '/account/patients', icon: Users, label: `Bemorlar · ${patients.data?.length ?? 0}`, tone: 'from-violet-50 to-violet-100 text-violet-700' },
          { to: '/account/addresses', icon: MapPin, label: `Manzillar · ${addresses.data?.length ?? 0}`, tone: 'from-amber-50 to-amber-100 text-amber-700' },
        ].map((a) => (
          <Link key={a.to} to={a.to} className="card group flex flex-col gap-3 p-4 transition hover:-translate-y-0.5 hover:shadow-lift">
            <span className={`flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br ${a.tone}`}>
              <a.icon className="h-5 w-5" />
            </span>
            <span className="font-bold text-ink-900">{a.label}</span>
          </Link>
        ))}
      </div>

      {upcoming.length > 0 && (
        <section>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-bold text-ink-900">Faol bronlar</h2>
            <Link to="/account/bookings" className="text-sm font-semibold text-brand-600">
              Barchasi →
            </Link>
          </div>
          <div className="space-y-3">
            {upcoming.slice(0, 3).map((b) => (
              <BookingCard key={b.id} b={b} />
            ))}
          </div>
        </section>
      )}

      {!!favs.data?.length && (
        <section>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-lg font-bold text-ink-900">
              <Heart className="h-5 w-5 fill-rose-500 text-rose-500" /> Sevimli shifokorlar
            </h2>
            <Link to="/account/favorites" className="text-sm font-semibold text-brand-600">
              Barchasi →
            </Link>
          </div>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {favs.data.slice(0, 3).map((d, i) => (
              <DoctorCard key={d.id} d={d} index={i} />
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
