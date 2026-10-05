import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, Clock, FileText, Stethoscope } from 'lucide-react'
import { api } from '@/lib/api'
import { relativeDateTime } from '@/lib/format'
import { useSpecializations } from '@/lib/queries'
import { Avatar, EmptyState, ErrorState, ListSkeleton, PageHeader, Segmented, StatusBadge, cx } from '@/components/ui'
import { DOCTOR_STATUS, type ModerationDoctor } from './types'

type Tab = 'pending' | 'approved' | 'rejected' | 'suspended' | 'draft'

export function SlaBadge({ due }: { due: string | null }) {
  if (!due) return null
  const ms = new Date(due).getTime() - Date.now()
  const overdue = ms < 0
  const hours = Math.round(Math.abs(ms) / 3_600_000)
  const label = hours >= 24 ? `${Math.round(hours / 24)} kun` : `${hours} soat`
  return (
    <span className={cx('chip', overdue ? 'bg-rose-50 text-rose-700 ring-1 ring-rose-200' : hours < 12 ? 'bg-amber-50 text-amber-700' : 'bg-ink-50 text-ink-600')}>
      <Clock className="h-3.5 w-3.5" />
      {overdue ? `Muddat ${label} o'tgan` : `${label} qoldi`}
    </span>
  )
}

export default function DoctorsQueue() {
  const [tab, setTab] = useState<Tab>('pending')
  const specs = useSpecializations()
  const q = useQuery({
    queryKey: ['mod', 'doctors', tab],
    queryFn: () => api<ModerationDoctor[]>('/moderation/doctors', { query: { status: tab } }),
  })
  const specNames = (ids: string[]) => ids.map((id) => specs.data?.find((s) => s.id === id)?.name).filter(Boolean).join(', ')
  const list = [...(q.data ?? [])].sort((a, b) => (a.sla_due_at ?? '').localeCompare(b.sla_due_at ?? ''))

  return (
    <div>
      <PageHeader title="Shifokorlar moderatsiyasi" subtitle="Anketa va hujjatlarni tekshirib, tasdiqlang yoki rad eting" />
      <div className="mb-5 overflow-x-auto scrollbar-none">
        <Segmented
          value={tab}
          onChange={setTab}
          size="sm"
          options={[
            { value: 'pending', label: 'Navbatda', count: tab === 'pending' ? q.data?.length : undefined },
            { value: 'approved', label: 'Tasdiqlangan' },
            { value: 'rejected', label: 'Rad etilgan' },
            { value: 'suspended', label: "To'xtatilgan" },
            { value: 'draft', label: 'Qoralama' },
          ]}
        />
      </div>

      {q.isLoading ? (
        <ListSkeleton rows={4} />
      ) : q.error ? (
        <div className="card"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
      ) : list.length === 0 ? (
        <div className="card">
          <EmptyState icon={<Stethoscope className="h-7 w-7" />} title={tab === 'pending' ? 'Navbat bo‘sh 🎉' : "Ro'yxat bo'sh"} text={tab === 'pending' ? 'Barcha arizalar ko‘rib chiqilgan.' : undefined} />
        </div>
      ) : (
        <div className="space-y-3">
          {list.map((d) => (
            <Link key={d.id} to={`/admin/doctors/${d.id}`} className="card group flex items-center gap-4 p-4 transition hover:shadow-lift">
              <Avatar name={d.full_name} size={52} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="truncate font-bold text-ink-900">{d.full_name || 'Ism kiritilmagan'}</span>
                  <StatusBadge status={d.status} map={DOCTOR_STATUS} />
                </div>
                <div className="mt-0.5 truncate text-sm text-ink-500">
                  {specNames(d.specialization_ids) || 'Mutaxassislik ko‘rsatilmagan'} · {d.experience_years} yil tajriba
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {d.status === 'pending' && <SlaBadge due={d.sla_due_at} />}
                  <span className="chip bg-ink-50 text-ink-600"><FileText className="h-3.5 w-3.5" />{d.documents?.length ?? 0} hujjat</span>
                  {d.submitted_at && <span className="text-xs text-ink-400">Yuborilgan: {relativeDateTime(d.submitted_at)}</span>}
                </div>
              </div>
              <ChevronRight className="h-5 w-5 shrink-0 text-ink-300 transition group-hover:translate-x-0.5 group-hover:text-ink-500" />
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
