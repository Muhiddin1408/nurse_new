import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Building2, MapPin, Navigation, Star } from 'lucide-react'
import { useClinics } from '@/lib/queries'
import { Button, EmptyState, ErrorState, Skeleton, cx } from '@/components/ui'

const GRADIENTS = ['from-brand-400 to-teal-700', 'from-sky-400 to-indigo-600', 'from-violet-400 to-fuchsia-600', 'from-amber-400 to-orange-600']

export default function ClinicsPage() {
  const q = useClinics()
  return (
    <div>
      <section className="mesh-bg -mt-[72px] pb-10 pt-[100px]">
        <div className="container-x">
          <h1 className="text-3xl font-extrabold text-ink-950 sm:text-4xl">Klinikalar</h1>
          <p className="mt-2 max-w-xl text-ink-500">Zamonaviy uskunalar bilan jihozlangan filiallar. Klinikani tanlang — u yerda qabul qiladigan shifokorlarni ko'rasiz.</p>
        </div>
      </section>
      <div className="container-x">
        {q.isError ? (
          <div className="card"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
        ) : q.isLoading ? (
          <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-72 rounded-3xl" />)}</div>
        ) : !q.data?.length ? (
          <div className="card"><EmptyState icon={<Building2 className="h-7 w-7" />} title="Klinikalar hali qo'shilmagan" /></div>
        ) : (
          <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
            {q.data.map((c, i) => (
              <motion.div key={c.id} initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }} className="card overflow-hidden">
                <div className={cx('relative h-36 bg-gradient-to-br', GRADIENTS[i % GRADIENTS.length])}>
                  <div className="absolute inset-0 bg-[radial-gradient(circle_at_80%_20%,rgba(255,255,255,.25),transparent_45%)]" />
                  <Building2 className="absolute bottom-4 right-4 h-20 w-20 text-white/20" />
                  {Number(c.rating) > 0 && (
                    <span className="chip absolute left-4 top-4 bg-white/90 text-ink-800">
                      <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-400" /> {Number(c.rating).toFixed(1)} · {c.reviews_count} sharh
                    </span>
                  )}
                </div>
                <div className="p-5">
                  <h3 className="text-lg font-bold text-ink-900">{c.name}</h3>
                  <p className="mt-1.5 flex items-start gap-1.5 text-sm text-ink-500">
                    <MapPin className="mt-0.5 h-4 w-4 shrink-0" /> {c.city}, {c.street}
                  </p>
                  <div className="mt-5 flex gap-2">
                    <Link to={`/doctors?clinic=${c.id}`} className="flex-1">
                      <Button block>Shifokorlar</Button>
                    </Link>
                    <a href={`https://www.google.com/maps/dir/?api=1&destination=${c.latitude},${c.longitude}`} target="_blank" rel="noreferrer">
                      <Button variant="outline" icon={<Navigation className="h-4 w-4" />}>
                        Yo'nalish
                      </Button>
                    </a>
                  </div>
                </div>
              </motion.div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
