import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Package } from 'lucide-react'
import { api } from '@/lib/api'
import { dateLong } from '@/lib/format'
import type { Enrollment } from '@/lib/types'
import { Badge, Button, EmptyState, ErrorState, ListSkeleton, PageHeader } from '@/components/ui'

export default function PackagesPage() {
  const q = useQuery({ queryKey: ['packages', 'mine'], queryFn: () => api<Enrollment[]>('/booking/packages/mine') })
  return (
    <div>
      <PageHeader title="Paketlarim" subtitle="Oldindan olingan seanslar va chegirmalar" />
      {q.isError ? (
        <div className="card"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
      ) : q.isLoading ? (
        <ListSkeleton rows={2} />
      ) : !q.data?.length ? (
        <div className="card">
          <EmptyState icon={<Package className="h-7 w-7" />} title="Faol paketlar yo'q" text="Davolanish kursi uchun paket oling — har bir seansda chegirma bo'ladi. Paketlar shifokor sahifasida." action={<Link to="/doctors"><Button>Shifokorlar</Button></Link>} />
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {q.data.map((e) => {
            const used = e.sessions_total - e.sessions_left
            const pct = Math.round((used / Math.max(1, e.sessions_total)) * 100)
            return (
              <div key={e.id} className="card overflow-hidden">
                <div className="bg-gradient-to-br from-violet-500 to-indigo-600 p-5 text-white">
                  <div className="flex items-start justify-between gap-3">
                    <div className="font-bold">{e.package_name}</div>
                    <Badge tone="violet" className="bg-white/90">−{e.discount_percent}%</Badge>
                  </div>
                  <div className="mt-1 text-sm text-white/75">{e.service_name}</div>
                </div>
                <div className="p-5">
                  <div className="flex items-end justify-between">
                    <div>
                      <div className="text-3xl font-extrabold text-ink-900">{e.sessions_left}</div>
                      <div className="text-sm text-ink-500">seans qoldi ({e.sessions_total} tadan)</div>
                    </div>
                    <div className="text-right text-xs text-ink-400">Amal qiladi:<br /><b className="text-ink-600">{dateLong(e.expires_at)}</b> gacha</div>
                  </div>
                  <div className="mt-4 h-2.5 overflow-hidden rounded-full bg-ink-100">
                    <div className="h-full rounded-full bg-gradient-to-r from-violet-500 to-indigo-500" style={{ width: `${pct}%` }} />
                  </div>
                  <Link to={`/book/${e.doctor_id}`} className="mt-4 block">
                    <Button block variant="secondary" disabled={e.sessions_left === 0}>Navbatdagi seansga yozilish</Button>
                  </Link>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
