import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import {
  Activity, Baby, BadgeCheck, Brain, Ear, Eye, Heart, HeartPulse, House, MapPin, Smile, Sparkles, Stethoscope, Syringe, Venus,
  type LucideIcon,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { useFavorites } from '@/lib/queries'
import { useToast } from '@/lib/toast'
import type { DoctorList } from '@/lib/types'
import { Avatar, Button, RatingPill, cx } from './ui'

const SPEC_ICONS: Record<string, LucideIcon> = {
  terapevt: Stethoscope,
  kardiolog: HeartPulse,
  pediatr: Baby,
  nevrolog: Brain,
  stomatolog: Smile,
  ginekolog: Venus,
  dermatolog: Sparkles,
  oftalmolog: Eye,
  lor: Ear,
  hamshira: Syringe,
}

const SPEC_TONES = [
  'from-emerald-50 to-teal-100 text-teal-700',
  'from-rose-50 to-pink-100 text-rose-600',
  'from-sky-50 to-blue-100 text-sky-700',
  'from-violet-50 to-purple-100 text-violet-700',
  'from-amber-50 to-orange-100 text-amber-700',
  'from-cyan-50 to-cyan-100 text-cyan-700',
]

export function specIcon(slug?: string): LucideIcon {
  return (slug && SPEC_ICONS[slug]) || Activity
}

export function specTone(i: number) {
  return SPEC_TONES[i % SPEC_TONES.length]
}

export function FavoriteButton({ doctorId, className }: { doctorId: string; className?: string }) {
  const { isAuthed, user } = useAuth()
  const nav = useNavigate()
  const toast = useToast()
  const qc = useQueryClient()
  const favs = useFavorites()
  const isFav = !!favs.data?.some((d) => d.id === doctorId)

  const m = useMutation({
    mutationFn: () => api(`/catalog/doctors/${doctorId}/favorite`, { method: isFav ? 'DELETE' : 'PUT' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['favorites'] })
      toast.success(isFav ? "Sevimlilardan olib tashlandi" : "Sevimlilarga qo'shildi")
    },
    onError: (e) => toast.error(e),
  })

  return (
    <button
      type="button"
      aria-label="Sevimli"
      onClick={(e) => {
        e.preventDefault()
        e.stopPropagation()
        if (!isAuthed) return nav('/login')
        if (user?.active_role !== 'client') return toast.info('Sevimlilar mijoz rejimida ishlaydi')
        m.mutate()
      }}
      disabled={m.isPending}
      className={cx('flex h-10 w-10 items-center justify-center rounded-2xl bg-white/90 ring-1 ring-ink-100 transition hover:scale-105', className)}
    >
      <Heart className={cx('h-5 w-5 transition', isFav ? 'fill-rose-500 text-rose-500' : 'text-ink-400')} />
    </button>
  )
}

export function DoctorCard({ d, index = 0 }: { d: DoctorList; index?: number }) {
  return (
    <motion.div initial={{ opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ delay: Math.min(index * 0.05, 0.3) }}>
      <Link to={`/doctors/${d.id}`} className="card group relative flex h-full flex-col p-5 transition hover:-translate-y-1 hover:shadow-lift">
        <FavoriteButton doctorId={d.id} className="absolute right-4 top-4" />
        <div className="flex items-start gap-4 pr-10">
          <div className="relative">
            <Avatar name={d.full_name.replace(/^Dr\.?\s*/, '')} size={64} />
            <span className="absolute -bottom-1 -right-1 flex h-6 w-6 items-center justify-center rounded-full bg-white">
              <BadgeCheck className="h-5 w-5 fill-brand-500 text-white" />
            </span>
          </div>
          <div className="min-w-0">
            <h3 className="line-clamp-2 text-[17px] font-bold leading-snug text-ink-900 group-hover:text-brand-700">{d.full_name}</h3>
            <p className="mt-0.5 truncate text-sm font-medium text-brand-700">{d.specializations.join(', ') || 'Shifokor'}</p>
            <p className="mt-1 text-sm text-ink-500">{d.experience_years} yillik tajriba</p>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <RatingPill rating={d.rating ? Number(d.rating) : null} count={d.reviews_count} isNew={d.is_new} />
          {d.accepts_home_visits && (
            <span className="chip bg-brand-50 text-brand-700">
              <House className="h-3.5 w-3.5" /> Uyga chaqiruv
            </span>
          )}
          {d.distance_km != null && (
            <span className="chip bg-ink-100 text-ink-600">
              <MapPin className="h-3.5 w-3.5" /> {d.distance_km.toFixed(1)} km
            </span>
          )}
        </div>
        <div className="mt-auto pt-5">
          <Button block variant="secondary" className="group-hover:bg-brand-600 group-hover:text-white">
            Qabulga yozilish
          </Button>
        </div>
      </Link>
    </motion.div>
  )
}

export function DoctorCardSkeleton() {
  return (
    <div className="card p-5">
      <div className="flex gap-4">
        <div className="h-16 w-16 animate-pulse rounded-2xl bg-ink-100" />
        <div className="flex-1 space-y-2 pt-1">
          <div className="h-4 w-3/4 animate-pulse rounded bg-ink-100" />
          <div className="h-3 w-1/2 animate-pulse rounded bg-ink-100" />
          <div className="h-3 w-1/3 animate-pulse rounded bg-ink-100" />
        </div>
      </div>
      <div className="mt-5 h-11 animate-pulse rounded-2xl bg-ink-100" />
    </div>
  )
}
