import { Link } from 'react-router-dom'
import { Heart } from 'lucide-react'
import { useFavorites } from '@/lib/queries'
import { DoctorCard, DoctorCardSkeleton } from '@/components/client'
import { Button, EmptyState, ErrorState, PageHeader } from '@/components/ui'

export default function FavoritesPage() {
  const q = useFavorites()
  return (
    <div>
      <PageHeader title="Sevimli shifokorlar" subtitle="Tez qayta yozilish uchun saqlangan shifokorlar" />
      {q.isError ? (
        <div className="card"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
      ) : q.isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{[0, 1, 2].map((i) => <DoctorCardSkeleton key={i} />)}</div>
      ) : !q.data?.length ? (
        <div className="card">
          <EmptyState icon={<Heart className="h-7 w-7" />} title="Sevimlilar bo'sh" text="Shifokor kartochkasidagi ♥ belgisini bosing — u shu yerda paydo bo'ladi." action={<Link to="/doctors"><Button>Shifokorlarni ko'rish</Button></Link>} />
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {q.data.map((d, i) => <DoctorCard key={d.id} d={d} index={i} />)}
        </div>
      )}
    </div>
  )
}
