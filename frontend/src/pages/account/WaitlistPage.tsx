import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BellRing, Building2, House, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { dateShort } from '@/lib/format'
import { useToast } from '@/lib/toast'
import type { WaitlistEntry } from '@/lib/types'
import { Avatar, Badge, Button, EmptyState, ErrorState, ListSkeleton, PageHeader } from '@/components/ui'

const STATUS: Record<string, { l: string; t: 'green' | 'blue' | 'gray' | 'amber' }> = {
  active: { l: 'Kutilmoqda', t: 'amber' },
  notified: { l: 'Joy bo‘shadi!', t: 'green' },
  converted: { l: 'Bron qilindi', t: 'blue' },
  expired: { l: 'Muddati tugadi', t: 'gray' },
  cancelled: { l: 'Bekor qilindi', t: 'gray' },
}

export default function WaitlistPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const q = useQuery({ queryKey: ['waitlist'], queryFn: () => api<WaitlistEntry[]>('/booking/waitlist') })
  const del = useMutation({
    mutationFn: (id: string) => api(`/booking/waitlist/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['waitlist'] })
      toast.success('Navbatdan chiqdingiz')
    },
    onError: (e) => toast.error(e),
  })

  return (
    <div>
      <PageHeader title="Navbatlar" subtitle="Joy bo'shashi bilan sizga birinchi bo'lib xabar beramiz" />
      {q.isError ? (
        <div className="card"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
      ) : q.isLoading ? (
        <ListSkeleton rows={2} />
      ) : !q.data?.length ? (
        <div className="card">
          <EmptyState icon={<BellRing className="h-7 w-7" />} title="Navbatlar yo'q" text="Shifokorda bo'sh vaqt bo'lmasa, uning sahifasida «Bo'shasa xabar bering» tugmasini bosing." />
        </div>
      ) : (
        <div className="space-y-3">
          {q.data.map((w) => {
            const st = STATUS[w.status] ?? { l: w.status, t: 'gray' as const }
            return (
              <div key={w.id} className="card flex flex-wrap items-center gap-4 p-5">
                <Avatar name={w.doctor_name.replace(/^Dr\.?\s*/, '')} size={48} />
                <div className="min-w-0 flex-1">
                  <div className="font-bold text-ink-900">{w.doctor_name}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-ink-500">
                    <Badge tone={st.t} dot>{st.l}</Badge>
                    <span className="inline-flex items-center gap-1">{w.place === 'home' ? <House className="h-3.5 w-3.5" /> : <Building2 className="h-3.5 w-3.5" />}{w.place === 'home' ? 'Uyda' : 'Klinikada'}</span>
                    <span>{dateShort(w.date_from)} – {dateShort(w.date_to)}</span>
                  </div>
                </div>
                <div className="flex gap-2">
                  <Link to={`/doctors/${w.doctor_id}`}><Button size="sm" variant="secondary">Vaqtlarni ko'rish</Button></Link>
                  {w.status === 'active' && (
                    <Button size="sm" variant="ghost" loading={del.isPending && del.variables === w.id} onClick={() => del.mutate(w.id)} icon={<Trash2 className="h-4 w-4" />} aria-label="Navbatdan chiqish" />
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
