import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useInfiniteQuery, useMutation, useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import {
  ArrowLeft, BadgeCheck, BellRing, Building2, CalendarCheck2, Clock, GraduationCap, House, Languages, MapPin, MessageSquareText, Package,
  ShieldCheck, Star,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { addDaysISO, dateLong, minutes, money, todayISO } from '@/lib/format'
import { useDoctor, useDoctorServices } from '@/lib/queries'
import { useToast } from '@/lib/toast'
import type { PackageOffer } from '@/lib/types'
import { FavoriteButton } from '@/components/client'
import { SlotPicker, slotDay, useFirstFreeDay, type Place, type PlacedSlot } from '@/components/SlotPicker'
import { Avatar, Badge, Button, EmptyState, ErrorState, Modal, Money, RatingPill, Segmented, Skeleton, Stars, cx } from '@/components/ui'

interface Review {
  id: string
  rating: number
  comment: string
  author: string | null
  is_anonymous: boolean
  created_at: string
  doctor_reply: string | null
  doctor_replied_at: string | null
}

export default function DoctorPage() {
  const { id } = useParams()
  const nav = useNavigate()
  const doctor = useDoctor(id)
  const services = useDoctorServices(id)
  const [place, setPlace] = useState<Place>('clinic')
  const [date, setDate] = useState(todayISO())
  const firstFree = useFirstFreeDay(id, place)

  useEffect(() => {
    if (firstFree.data) setDate(slotDay(firstFree.data))
  }, [firstFree.data])

  const hasHome = !!services.data?.some((s) => s.place === 'home')
  const hasClinic = !!services.data?.some((s) => s.place === 'clinic')
  useEffect(() => {
    if (services.data && !hasClinic && hasHome) setPlace('home')
  }, [services.data, hasClinic, hasHome])

  const clinicNames = useMemo(() => Object.fromEntries((doctor.data?.clinics ?? []).map((c) => [c.id, c.name])), [doctor.data])
  const placeServices = services.data?.filter((s) => s.place === place) ?? []
  const minPrice = placeServices.length ? placeServices.reduce((m, s) => (Number(s.price) < Number(m) ? s.price : m), placeServices[0].price) : null

  const book = (slot?: PlacedSlot) => {
    const p = new URLSearchParams({ place })
    if (slot) {
      p.set('slot', slot.id)
      p.set('date', slotDay(slot))
    } else p.set('date', date)
    nav(`/book/${id}?${p}`)
  }

  if (doctor.isError)
    return (
      <div className="container-x py-16">
        <div className="card">
          <ErrorState error={doctor.error} onRetry={() => doctor.refetch()} />
        </div>
      </div>
    )

  const d = doctor.data
  const name = d?.full_name.replace(/^Dr\.?\s*/, '') ?? ''

  return (
    <div className="pb-24 lg:pb-0">
      <section className="mesh-bg -mt-[72px] pb-10 pt-[96px]">
        <div className="container-x">
          <Link to="/doctors" className="mb-5 inline-flex items-center gap-1.5 text-sm font-semibold text-ink-500 hover:text-ink-900">
            <ArrowLeft className="h-4 w-4" /> Shifokorlar
          </Link>
          {!d ? (
            <div className="flex gap-5">
              <Skeleton className="h-28 w-28 rounded-3xl" />
              <div className="flex-1 space-y-3 pt-2">
                <Skeleton className="h-7 w-64" />
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-4 w-52" />
              </div>
            </div>
          ) : (
            <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="flex flex-col gap-6 sm:flex-row sm:items-start">
              <div className="relative self-start">
                <Avatar name={name} size={112} className="rounded-[2rem] text-4xl shadow-lift" />
                <span className="absolute -bottom-2 -right-2 flex h-9 w-9 items-center justify-center rounded-full bg-white shadow-soft">
                  <BadgeCheck className="h-7 w-7 fill-brand-500 text-white" />
                </span>
              </div>
              <div className="flex-1">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h1 className="text-3xl font-extrabold text-ink-950 sm:text-4xl">{d.full_name}</h1>
                    <p className="mt-1 text-lg font-semibold text-brand-700">{d.specializations.join(' · ')}</p>
                  </div>
                  <FavoriteButton doctorId={d.id} className="shrink-0" />
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <RatingPill rating={d.rating ? Number(d.rating) : null} count={d.reviews_count} isNew={d.is_new} />
                  <span className="chip bg-white text-ink-700 ring-1 ring-ink-100">
                    <GraduationCap className="h-3.5 w-3.5" /> {d.experience_years} yillik tajriba
                  </span>
                  {d.accepts_home_visits && (
                    <span className="chip bg-brand-50 text-brand-700">
                      <House className="h-3.5 w-3.5" /> Uyga chaqiruv · {d.home_visit_radius_km} km gacha
                    </span>
                  )}
                  <span className="chip bg-white text-ink-700 ring-1 ring-ink-100">
                    <ShieldCheck className="h-3.5 w-3.5 text-brand-600" /> Litsenziya: {d.license_number || 'tekshirilgan'}
                  </span>
                </div>
              </div>
            </motion.div>
          )}
        </div>
      </section>

      <div className="container-x grid grid-cols-1 gap-8 lg:grid-cols-[1fr_400px]">
        <div className="min-w-0 space-y-6">
          {d?.bio && (
            <Section title="Shifokor haqida" icon={<GraduationCap className="h-5 w-5" />}>
              <p className="whitespace-pre-line text-[15px] leading-relaxed text-ink-600">{d.bio}</p>
              <div className="mt-4 flex flex-wrap gap-2 text-sm text-ink-500">
                <span className="inline-flex items-center gap-1.5">
                  <Languages className="h-4 w-4" /> O'zbek, Rus tillari
                </span>
              </div>
            </Section>
          )}

          <Section title="Xizmatlar va narxlar" icon={<CalendarCheck2 className="h-5 w-5" />} action={hasHome && hasClinic ? <PlaceSwitch place={place} onChange={setPlace} /> : undefined}>
            {services.isLoading ? (
              <div className="space-y-2">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-16 rounded-2xl" />
                ))}
              </div>
            ) : placeServices.length === 0 ? (
              <p className="text-ink-500">Bu yo'nalishda xizmatlar hozircha yo'q.</p>
            ) : (
              <div className="divide-y divide-ink-100">
                {placeServices.map((s) => (
                  <div key={s.id} className="flex items-center justify-between gap-4 py-3.5">
                    <div>
                      <div className="font-semibold text-ink-900">{s.name}</div>
                      <div className="mt-0.5 flex items-center gap-1.5 text-sm text-ink-500">
                        <Clock className="h-3.5 w-3.5" /> {minutes(s.duration_minutes)}
                      </div>
                    </div>
                    <div className="text-right font-bold text-ink-900">
                      <Money value={s.price} />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Section>

          {!!d?.clinics.length && (
            <Section title="Qabul manzillari" icon={<Building2 className="h-5 w-5" />}>
              <div className="grid gap-3 sm:grid-cols-2">
                {d.clinics.map((c) => (
                  <a
                    key={c.id}
                    href={`https://www.google.com/maps/search/?api=1&query=${c.latitude},${c.longitude}`}
                    target="_blank"
                    rel="noreferrer"
                    className="group flex items-start gap-3 rounded-2xl bg-ink-50 p-4 transition hover:bg-brand-50"
                  >
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white text-brand-600 shadow-soft">
                      <MapPin className="h-5 w-5" />
                    </span>
                    <div>
                      <div className="font-bold text-ink-900">{c.name}</div>
                      <div className="text-sm text-ink-500">
                        {c.city}, {c.street}
                      </div>
                      <div className="mt-1 text-xs font-semibold text-brand-600 opacity-0 transition group-hover:opacity-100">Xaritada ochish →</div>
                    </div>
                  </a>
                ))}
              </div>
            </Section>
          )}

          {id && <Packages doctorId={id} />}
          {id && <Reviews doctorId={id} rating={d?.rating ? Number(d.rating) : null} count={d?.reviews_count ?? 0} />}
        </div>

        <aside className="min-w-0 lg:sticky lg:top-24 lg:self-start">
          <div className="card overflow-hidden">
            <div className="bg-gradient-to-br from-ink-900 to-ink-800 p-5 text-white">
              <div className="text-sm text-white/60">Qabul narxi</div>
              <div className="mt-1 text-2xl font-extrabold">{minPrice ? `${money(minPrice)} dan` : '—'}</div>
              {hasHome && hasClinic && (
                <div className="mt-4">
                  <PlaceSwitch place={place} onChange={setPlace} dark />
                </div>
              )}
            </div>
            <div className="p-5">
              <div className="mb-3 flex items-center justify-between">
                <h3 className="font-bold text-ink-900">Bo'sh vaqtlar</h3>
                {firstFree.data && <span className="text-xs font-semibold text-brand-600">Eng yaqini: {dateLong(slotDay(firstFree.data))}</span>}
              </div>
              {id && <SlotPicker doctorId={id} date={date} onDate={setDate} onSelect={(s) => book(s)} place={place} clinicNames={clinicNames} compact />}
              {firstFree.isSuccess && !firstFree.data && id && <WaitlistButton doctorId={id} place={place} />}
              <Button block size="lg" className="mt-5" onClick={() => book()}>
                Qabulga yozilish
              </Button>
              <p className="mt-3 text-center text-xs text-ink-400">Bekor qilish bepul — qabuldan 24 soat oldin</p>
            </div>
          </div>
        </aside>
      </div>

      <div className="fixed inset-x-0 bottom-0 z-40 border-t border-ink-100 bg-white/95 p-3 backdrop-blur-xl lg:hidden">
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <div className="text-xs text-ink-500">Narxi</div>
            <div className="font-extrabold text-ink-900">{minPrice ? `${money(minPrice)} dan` : '—'}</div>
          </div>
          <Button size="lg" onClick={() => book()}>
            Qabulga yozilish
          </Button>
        </div>
      </div>
    </div>
  )
}

function Section({ title, icon, action, children }: { title: string; icon?: React.ReactNode; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <motion.section initial={{ opacity: 0, y: 12 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} className="card p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2 className="flex items-center gap-2.5 text-lg font-bold text-ink-900">
          {icon && <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-50 text-brand-600">{icon}</span>}
          {title}
        </h2>
        {action}
      </div>
      {children}
    </motion.section>
  )
}

function PlaceSwitch({ place, onChange, dark }: { place: Place; onChange: (p: Place) => void; dark?: boolean }) {
  if (dark)
    return (
      <div className="grid grid-cols-2 gap-1 rounded-2xl bg-white/10 p-1">
        {(['clinic', 'home'] as Place[]).map((p) => (
          <button key={p} onClick={() => onChange(p)} className={cx('flex items-center justify-center gap-1.5 rounded-xl py-2 text-sm font-semibold transition', place === p ? 'bg-white text-ink-900' : 'text-white/70 hover:text-white')}>
            {p === 'clinic' ? <Building2 className="h-4 w-4" /> : <House className="h-4 w-4" />}
            {p === 'clinic' ? 'Klinikada' : 'Uyda'}
          </button>
        ))}
      </div>
    )
  return (
    <Segmented
      size="sm"
      value={place}
      onChange={onChange}
      options={[
        { value: 'clinic', label: 'Klinikada', icon: <Building2 className="h-4 w-4" /> },
        { value: 'home', label: 'Uyda', icon: <House className="h-4 w-4" /> },
      ]}
    />
  )
}

function WaitlistButton({ doctorId, place }: { doctorId: string; place: Place }) {
  const { isAuthed } = useAuth()
  const nav = useNavigate()
  const toast = useToast()
  const m = useMutation({
    mutationFn: () => api('/booking/waitlist', { method: 'POST', body: { doctor_id: doctorId, date_from: todayISO(), date_to: addDaysISO(todayISO(), 13), place } }),
    onSuccess: () => toast.success("Navbatga yozildingiz — joy bo'shashi bilan xabar beramiz"),
    onError: (e) => toast.error(e),
  })
  return (
    <div className="mt-4 rounded-2xl bg-amber-50 p-4 ring-1 ring-amber-100">
      <div className="flex items-start gap-3">
        <BellRing className="mt-0.5 h-5 w-5 text-amber-600" />
        <div className="flex-1">
          <div className="font-semibold text-amber-900">Yaqin kunlarda joy yo'q</div>
          <div className="text-sm text-amber-700">Bo'shasa birinchi bo'lib sizga xabar beramiz</div>
          <Button size="sm" variant="dark" className="mt-3" loading={m.isPending} disabled={m.isSuccess} onClick={() => (isAuthed ? m.mutate() : nav('/login'))}>
            {m.isSuccess ? 'Navbatdasiz ✓' : "Bo'shasa xabar bering"}
          </Button>
        </div>
      </div>
    </div>
  )
}

function Packages({ doctorId }: { doctorId: string }) {
  const { isAuthed } = useAuth()
  const nav = useNavigate()
  const toast = useToast()
  const [chosen, setChosen] = useState<PackageOffer | null>(null)
  // Endpoint JWT talab qiladi — paketlar faqat kirgan mijozga ko'rsatiladi
  const q = useQuery({ queryKey: ['packages', doctorId], queryFn: () => api<PackageOffer[]>(`/booking/doctors/${doctorId}/packages`), enabled: isAuthed })
  const enroll = useMutation({
    mutationFn: (id: string) => api(`/booking/packages/${id}/enroll`, { method: 'POST' }),
    onSuccess: () => {
      toast.success("Paket faollashtirildi! Keyingi bronlarda chegirma avtomatik qo'llanadi")
      setChosen(null)
    },
    onError: (e) => toast.error(e),
  })
  if (!q.data?.length) return null
  return (
    <Section title="Paketlar — tejab davolaning" icon={<Package className="h-5 w-5" />}>
      <div className="grid gap-3 sm:grid-cols-2">
        {q.data.map((p) => (
          <div key={p.id} className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-violet-50 to-sky-50 p-5 ring-1 ring-violet-100">
            <span className="absolute right-4 top-4 rounded-full bg-violet-600 px-2.5 py-1 text-xs font-bold text-white">−{p.discount_percent}%</span>
            <div className="pr-14 font-bold text-ink-900">{p.name}</div>
            <div className="mt-1 text-sm text-ink-500">
              {p.sessions} seans · {p.validity_days} kun amal qiladi
            </div>
            <div className="mt-3 text-sm">
              Tejaysiz: <b className="text-violet-700">{money(p.total_saving)}</b>
            </div>
            <Button size="sm" variant="dark" className="mt-4" onClick={() => (isAuthed ? setChosen(p) : nav('/login'))}>
              Paketni olish
            </Button>
          </div>
        ))}
      </div>
      <Modal
        open={!!chosen}
        onClose={() => setChosen(null)}
        title="Paketni faollashtirish"
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setChosen(null)}>
              Bekor
            </Button>
            <Button loading={enroll.isPending} onClick={() => chosen && enroll.mutate(chosen.id)}>
              Faollashtirish
            </Button>
          </>
        }
      >
        {chosen && (
          <div className="space-y-2 text-[15px] text-ink-600">
            <p>
              <b className="text-ink-900">{chosen.name}</b> — {chosen.sessions} ta seans, har birida {chosen.discount_percent}% chegirma.
            </p>
            <p>
              Bitta seans narxi: <Money value={chosen.unit_price} className="font-semibold text-ink-900" />. Jami tejash: <b className="text-violet-700">{money(chosen.total_saving)}</b>.
            </p>
            <p className="text-sm text-ink-500">To'lov har bir bron vaqtida amalga oshiriladi.</p>
          </div>
        )}
      </Modal>
    </Section>
  )
}

function Reviews({ doctorId, rating, count }: { doctorId: string; rating: number | null; count: number }) {
  const q = useInfiniteQuery({
    queryKey: ['reviews', doctorId],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => api<Review[]>(`/catalog/doctors/${doctorId}/reviews`, { auth: false, query: { limit: 5, offset: pageParam } }),
    getNextPageParam: (last, all) => (last.length < 5 ? undefined : all.length * 5),
  })
  const items = q.data?.pages.flat() ?? []
  return (
    <Section title="Bemorlar sharhlari" icon={<MessageSquareText className="h-5 w-5" />}>
      {count > 0 && (
        <div className="mb-5 flex items-center gap-4 rounded-2xl bg-amber-50/60 p-4">
          <div className="text-4xl font-extrabold text-ink-900">{rating ? rating.toFixed(1) : '—'}</div>
          <div>
            <Stars value={rating ?? 0} size={18} />
            <div className="mt-1 text-sm text-ink-500">{count} ta tasdiqlangan sharh</div>
          </div>
        </div>
      )}
      {q.isLoading ? (
        <Skeleton className="h-24 rounded-2xl" />
      ) : items.length === 0 ? (
        <EmptyState icon={<Star className="h-7 w-7" />} title="Hali sharh yo'q" text="Birinchi bo'lib qabulga yoziling va fikringizni qoldiring." className="py-8" />
      ) : (
        <div className="space-y-4">
          {items.map((r) => (
            <div key={r.id} className="rounded-2xl border border-ink-100 p-4">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <Avatar name={r.author || 'Anonim'} size={36} className="rounded-xl" />
                  <div>
                    <div className="font-semibold text-ink-900">{r.author || 'Anonim bemor'}</div>
                    <div className="text-xs text-ink-400">{dateLong(r.created_at)}</div>
                  </div>
                </div>
                <Stars value={r.rating} size={14} />
              </div>
              {r.comment && <p className="mt-3 text-[15px] leading-relaxed text-ink-700">{r.comment}</p>}
              {r.doctor_reply && (
                <div className="mt-3 rounded-xl bg-brand-50/70 p-3 text-sm">
                  <Badge tone="green">Shifokor javobi</Badge>
                  <p className="mt-2 text-ink-700">{r.doctor_reply}</p>
                </div>
              )}
            </div>
          ))}
          {q.hasNextPage && (
            <Button variant="outline" block loading={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>
              Yana sharhlar
            </Button>
          )}
        </div>
      )}
    </Section>
  )
}
