import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'framer-motion'
import { Check, Flag, MessageSquareWarning, X } from 'lucide-react'
import { api } from '@/lib/api'
import { relativeDateTime } from '@/lib/format'
import { useToast } from '@/lib/toast'
import { Avatar, Badge, Button, EmptyState, ErrorState, ListSkeleton, PageHeader, Stars } from '@/components/ui'
import { REVIEW_FLAG, type ModReview } from './types'

export default function Reviews() {
  const qc = useQueryClient()
  const toast = useToast()
  const [busy, setBusy] = useState<string | null>(null)
  const q = useQuery({ queryKey: ['mod', 'reviews'], queryFn: () => api<ModReview[]>('/moderation/reviews', { query: { limit: 50 } }) })

  const act = useMutation({
    mutationFn: ({ r, action }: { r: ModReview; action: 'approve' | 'reject' }) => api<ModReview>(`/moderation/reviews/${r.id}/${action}`, { method: 'POST' }),
    onMutate: ({ r }) => setBusy(r.id),
    onSettled: () => setBusy(null),
    onSuccess: (_, { action }) => {
      toast.success(action === 'approve' ? "Sharh e'lon qilindi" : 'Sharh rad etildi')
      qc.invalidateQueries({ queryKey: ['mod', 'reviews'] })
    },
    onError: (e) => toast.error(e),
  })

  const list = q.data ?? []

  return (
    <div className="max-w-4xl">
      <PageHeader title="Sharhlar moderatsiyasi" subtitle="Avtomatik filtr shubhali deb belgilagan sharhlar — eng eskisi birinchi" />
      {q.isLoading ? (
        <ListSkeleton rows={3} />
      ) : q.error ? (
        <div className="card"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
      ) : list.length === 0 ? (
        <div className="card"><EmptyState icon={<MessageSquareWarning className="h-7 w-7" />} title="Tekshiriladigan sharh yo'q" text="Yangi shubhali sharhlar shu yerda paydo bo'ladi." /></div>
      ) : (
        <div className="space-y-4">
          <AnimatePresence initial={false}>
            {list.map((r) => (
              <motion.div key={r.id} layout exit={{ opacity: 0, x: 40 }} className="card p-5">
                <div className="flex items-start gap-3">
                  <Avatar name={r.is_anonymous ? 'Anonim' : r.author ?? '?'} size={42} className="rounded-xl" />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-bold text-ink-900">{r.is_anonymous ? 'Anonim' : r.author ?? 'Mijoz'}</span>
                      <Stars value={r.rating} size={14} />
                    </div>
                    <div className="text-xs text-ink-400">{relativeDateTime(r.created_at)} · Bron {r.booking_number}</div>
                  </div>
                </div>
                {r.moderation_flags.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {r.moderation_flags.map((f) => (
                      <Badge key={f} tone="red"><Flag className="h-3 w-3" />{REVIEW_FLAG[f] ?? f}</Badge>
                    ))}
                  </div>
                )}
                <p className="mt-3 whitespace-pre-line rounded-2xl bg-ink-50 p-4 text-[15px] leading-relaxed text-ink-800">{r.comment || <i className="text-ink-400">Izohsiz</i>}</p>
                <div className="mt-4 flex justify-end gap-2">
                  <Button variant="outline" size="sm" className="text-rose-600" icon={<X className="h-4 w-4" />} disabled={busy === r.id} onClick={() => act.mutate({ r, action: 'reject' })}>
                    Rad etish
                  </Button>
                  <Button size="sm" icon={<Check className="h-4 w-4" />} loading={busy === r.id && act.variables?.action === 'approve'} disabled={busy === r.id} onClick={() => act.mutate({ r, action: 'approve' })}>
                    E'lon qilish
                  </Button>
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  )
}
