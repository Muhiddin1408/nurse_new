import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useInfiniteQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { Building2, House, LocateFixed, Search, SlidersHorizontal, Star, X } from 'lucide-react'
import { api, asList } from '@/lib/api'
import { useClinics, useSpecializations } from '@/lib/queries'
import { useToast } from '@/lib/toast'
import type { DoctorList } from '@/lib/types'
import { DoctorCard, DoctorCardSkeleton, specIcon } from '@/components/client'
import { Button, EmptyState, ErrorState, Modal, Select, Toggle, cx } from '@/components/ui'

const PAGE = 12
const SORTS = [
  { v: 'relevance', l: 'Mosligi' },
  { v: 'rating', l: 'Reyting' },
  { v: 'price', l: 'Narx' },
  { v: 'distance', l: 'Masofa' },
]

export default function DoctorsPage() {
  const [sp, setSp] = useSearchParams()
  const toast = useToast()
  const specs = useSpecializations()
  const clinics = useClinics()
  const [filtersOpen, setFiltersOpen] = useState(false)

  const q = sp.get('q') ?? ''
  const specialization = sp.get('specialization') ?? ''
  const clinic = sp.get('clinic') ?? ''
  const home = sp.get('home') === '1'
  const minRating = sp.get('min_rating') ?? ''
  const sort = sp.get('sort') ?? 'relevance'
  const lat = sp.get('lat') ?? ''
  const lng = sp.get('lng') ?? ''

  const [text, setText] = useState(q)
  useEffect(() => setText(q), [q])
  useEffect(() => {
    const t = setTimeout(() => text !== q && update({ q: text }), 300)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text])

  function update(patch: Record<string, string | null>) {
    const next = new URLSearchParams(sp)
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === '') next.delete(k)
      else next.set(k, v)
    }
    setSp(next, { replace: true })
  }

  const params = { q, specialization, clinic, home, minRating, sort, lat, lng }
  const query = useInfiniteQuery({
    queryKey: ['doctor-search', params],
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      // Klinika filtri faqat /catalog/doctors da bor
      if (clinic) {
        const rows = asList<DoctorList>(
          await api('/catalog/doctors', { auth: false, query: { clinic, specialization, home_only: home ? 1 : undefined, limit: PAGE, offset: pageParam } }),
        )
        return { results: rows, total: null as number | null }
      }
      return api<{ results: DoctorList[]; total: number | null }>('/catalog/search', {
        auth: false,
        query: {
          q,
          specialization,
          home_only: home ? 1 : undefined,
          min_rating: minRating,
          sort,
          lat,
          lng,
          limit: PAGE,
          offset: pageParam,
        },
      })
    },
    getNextPageParam: (last, all) => (last.results.length < PAGE ? undefined : all.length * PAGE),
  })

  const doctors = useMemo(() => query.data?.pages.flatMap((p) => p.results) ?? [], [query.data])
  const total = query.data?.pages[0]?.total
  const activeSpec = specs.data?.find((s) => s.id === specialization)
  const activeClinic = clinics.data?.find((c) => c.id === clinic)
  const activeCount = [specialization, clinic, home ? '1' : '', minRating].filter(Boolean).length

  const locate = () => {
    if (!navigator.geolocation) return toast.error('Brauzeringiz geolokatsiyani qo‘llab-quvvatlamaydi')
    navigator.geolocation.getCurrentPosition(
      (pos) => update({ lat: pos.coords.latitude.toFixed(5), lng: pos.coords.longitude.toFixed(5), sort: 'distance' }),
      () => toast.error('Joylashuvni aniqlab bo‘lmadi — ruxsat bering'),
      { timeout: 8000 },
    )
  }

  const filters = (
    <div className="space-y-6">
      <div>
        <div className="label">Mutaxassislik</div>
        <div className="flex flex-wrap gap-2">
          <Chip active={!specialization} onClick={() => update({ specialization: null })}>
            Barchasi
          </Chip>
          {specs.data?.map((s) => {
            const Icon = specIcon(s.slug)
            return (
              <Chip key={s.id} active={specialization === s.id} onClick={() => update({ specialization: s.id })}>
                <Icon className="h-3.5 w-3.5" /> {s.name}
              </Chip>
            )
          })}
        </div>
      </div>
      <div>
        <div className="label">Klinika</div>
        <Select value={clinic} onChange={(e) => update({ clinic: e.target.value })}>
          <option value="">Istalgan klinika</option>
          {clinics.data?.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </Select>
      </div>
      <div>
        <div className="label">Minimal reyting</div>
        <div className="flex flex-wrap gap-2">
          {['', '4', '4.5'].map((r) => (
            <Chip key={r} active={minRating === r} onClick={() => update({ min_rating: r })} disabled={!!clinic}>
              {r ? (
                <>
                  <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-400" /> {r}+
                </>
              ) : (
                'Istalgan'
              )}
            </Chip>
          ))}
        </div>
      </div>
      <div className="flex items-center justify-between rounded-2xl bg-brand-50/60 p-4 ring-1 ring-brand-100">
        <div className="flex items-center gap-3">
          <House className="h-5 w-5 text-brand-600" />
          <div>
            <div className="font-semibold text-ink-900">Uyga chaqiruv</div>
            <div className="text-xs text-ink-500">Faqat uyga keladigan shifokorlar</div>
          </div>
        </div>
        <Toggle checked={home} onChange={(v) => update({ home: v ? '1' : null })} />
      </div>
      {activeCount > 0 && (
        <Button variant="ghost" block onClick={() => update({ specialization: null, clinic: null, home: null, min_rating: null })}>
          Filtrlarni tozalash
        </Button>
      )}
    </div>
  )

  return (
    <div>
      <section className="mesh-bg -mt-[72px] pb-8 pt-[100px]">
        <div className="container-x">
          <h1 className="text-3xl font-extrabold text-ink-950 sm:text-4xl">
            {activeSpec ? `${activeSpec.name}lar` : home ? 'Uyga chaqiruv' : activeClinic ? activeClinic.name : 'Shifokorlar'}
          </h1>
          <p className="mt-2 text-ink-500">Tasdiqlangan mutaxassislar, real bo'sh vaqtlar va halol sharhlar</p>
          <div className="mt-6 flex flex-col gap-3 sm:flex-row">
            <div className="flex flex-1 items-center gap-3 rounded-2xl bg-white px-4 shadow-soft ring-1 ring-ink-100 focus-within:ring-2 focus-within:ring-brand-400">
              <Search className="h-5 w-5 text-ink-400" />
              <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Ism yoki mutaxassislik bo'yicha qidirish..." className="h-14 w-full bg-transparent text-[15px]" />
              {text && (
                <button onClick={() => setText('')} className="text-ink-400 hover:text-ink-700">
                  <X className="h-5 w-5" />
                </button>
              )}
            </div>
            <div className="flex gap-2">
              <Button variant="outline" size="lg" className="lg:hidden" onClick={() => setFiltersOpen(true)} icon={<SlidersHorizontal className="h-5 w-5" />}>
                Filtr{activeCount ? ` · ${activeCount}` : ''}
              </Button>
              <Button variant="outline" size="lg" onClick={locate} icon={<LocateFixed className="h-5 w-5" />} className={cx(lat && 'border-brand-300 text-brand-700')}>
                <span className="hidden sm:inline">Yaqinimda</span>
              </Button>
            </div>
          </div>
        </div>
      </section>

      <div className="container-x grid gap-8 lg:grid-cols-[280px_1fr]">
        <aside className="hidden lg:block">
          <div className="card sticky top-24 p-5">{filters}</div>
        </aside>
        <div>
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm text-ink-500">
              {query.isLoading ? 'Qidirilmoqda...' : <>Topildi: <b className="text-ink-900">{total ?? doctors.length}</b> ta shifokor</>}
            </div>
            <div className="flex items-center gap-1 overflow-x-auto rounded-2xl bg-white p-1 ring-1 ring-ink-100 scrollbar-none">
              {SORTS.map((s) => (
                <button
                  key={s.v}
                  disabled={!!clinic}
                  onClick={() => (s.v === 'distance' && !lat ? locate() : update({ sort: s.v === 'relevance' ? null : s.v }))}
                  className={cx('whitespace-nowrap rounded-xl px-3 py-1.5 text-sm font-semibold transition disabled:opacity-40', sort === s.v ? 'bg-ink-900 text-white' : 'text-ink-500 hover:text-ink-900')}
                >
                  {s.l}
                </button>
              ))}
            </div>
          </div>

          {(activeSpec || activeClinic || home) && (
            <div className="mb-5 flex flex-wrap gap-2">
              {activeSpec && <ActiveTag onClear={() => update({ specialization: null })}>{activeSpec.name}</ActiveTag>}
              {activeClinic && (
                <ActiveTag onClear={() => update({ clinic: null })}>
                  <Building2 className="h-3.5 w-3.5" /> {activeClinic.name}
                </ActiveTag>
              )}
              {home && <ActiveTag onClear={() => update({ home: null })}>Uyga chaqiruv</ActiveTag>}
            </div>
          )}

          {query.isError ? (
            <div className="card">
              <ErrorState error={query.error} onRetry={() => query.refetch()} />
            </div>
          ) : query.isLoading ? (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <DoctorCardSkeleton key={i} />
              ))}
            </div>
          ) : doctors.length === 0 ? (
            <div className="card">
              <EmptyState
                icon={<Search className="h-7 w-7" />}
                title="Hech kim topilmadi"
                text="Qidiruv so'zini o'zgartiring yoki filtrlarni kamaytiring."
                action={
                  <Button variant="outline" onClick={() => setSp(new URLSearchParams(), { replace: true })}>
                    Filtrlarni tozalash
                  </Button>
                }
              />
            </div>
          ) : (
            <>
              <motion.div layout className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {doctors.map((d, i) => (
                  <DoctorCard key={d.id} d={d} index={i % PAGE} />
                ))}
              </motion.div>
              {query.hasNextPage && (
                <div className="mt-8 flex justify-center">
                  <Button variant="outline" loading={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>
                    Yana ko'rsatish
                  </Button>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <Modal open={filtersOpen} onClose={() => setFiltersOpen(false)} title="Filtrlar" footer={<Button block onClick={() => setFiltersOpen(false)}>Natijalarni ko'rish</Button>}>
        {filters}
      </Modal>
    </div>
  )
}

function Chip({ active, children, onClick, disabled }: { active: boolean; children: React.ReactNode; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cx(
        'inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-semibold transition disabled:opacity-40',
        active ? 'bg-ink-900 text-white' : 'bg-ink-100 text-ink-600 hover:bg-ink-200',
      )}
    >
      {children}
    </button>
  )
}

function ActiveTag({ children, onClear }: { children: React.ReactNode; onClear: () => void }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-brand-50 py-1 pl-3 pr-1 text-sm font-semibold text-brand-700 ring-1 ring-brand-100">
      {children}
      <button onClick={onClear} className="rounded-full p-1 hover:bg-brand-100">
        <X className="h-3.5 w-3.5" />
      </button>
    </span>
  )
}
