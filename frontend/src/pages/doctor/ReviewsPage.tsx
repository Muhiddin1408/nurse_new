import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { MessageSquareQuote, Reply, Star } from 'lucide-react'
import { api } from '@/lib/api'
import { useToast } from '@/lib/toast'
import { dateLong } from '@/lib/format'
import { Avatar, Button, EmptyState, ErrorState, ListSkeleton, PageHeader, Stars, Textarea, cx } from '@/components/ui'
import { useMyClinics, type Review } from './shared'

const PAGE = 20

export default function ReviewsPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const [limit, setLimit] = useState(PAGE)
  const [replyFor, setReplyFor] = useState<string | null>(null)
  const [text, setText] = useState('')
  const pub = useMyClinics()

  const q = useQuery({
    queryKey: ['doctor', 'reviews', limit],
    queryFn: () => api<Review[]>('/doctor/reviews', { query: { limit, offset: 0 } }),
    placeholderData: (prev) => prev,
  })

  const reply = useMutation({
    mutationFn: (id: string) => api(`/doctor/reviews/${id}/reply`, { method: 'POST', body: { text: text.trim() } }),
    onSuccess: () => {
      toast.success('Javobingiz e‘lon qilindi')
      setReplyFor(null)
      setText('')
      qc.invalidateQueries({ queryKey: ['doctor', 'reviews'] })
    },
    onError: (e) => toast.error(e),
  })

  const list = q.data ?? []
  const dist = [5, 4, 3, 2, 1].map((r) => ({ r, n: list.filter((x) => x.rating === r).length }))
  const avg = pub.data?.rating ?? (list.length ? list.reduce((s, x) => s + x.rating, 0) / list.length : null)
  const total = pub.data?.reviews_count ?? list.length

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Sharhlar" subtitle="Mijozlar fikri — reytingingiz shu sharhlardan hisoblanadi" />

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="card h-fit p-6 text-center">
          <div className="text-5xl font-extrabold text-ink-900">{avg ? Number(avg).toFixed(1) : '—'}</div>
          <div className="mt-2 flex justify-center">
            <Stars value={Number(avg ?? 0)} size={22} />
          </div>
          <div className="mt-1 text-sm text-ink-500">{total} ta sharh</div>
          <div className="mt-5 space-y-2">
            {dist.map(({ r, n }) => (
              <div key={r} className="flex items-center gap-2 text-sm">
                <span className="flex w-8 items-center gap-0.5 font-semibold text-ink-600">
                  {r} <Star className="h-3 w-3 fill-amber-400 text-amber-400" />
                </span>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-ink-100">
                  <div className="h-full rounded-full bg-gradient-to-r from-amber-400 to-amber-500" style={{ width: `${list.length ? (n / list.length) * 100 : 0}%` }} />
                </div>
                <span className="w-6 text-right text-ink-500">{n}</span>
              </div>
            ))}
          </div>
          {pub.data?.rating === null && <p className="mt-4 text-xs text-ink-400">Reyting 5 ta sharhdan keyin ochiq ko'rinadi.</p>}
        </div>

        <div className="space-y-3 lg:col-span-2">
          {q.isLoading && <ListSkeleton rows={4} />}
          {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
          {q.data && list.length === 0 && (
            <div className="card">
              <EmptyState icon={<MessageSquareQuote className="h-7 w-7" />} title="Hali sharhlar yo'q" text="Yakunlangan qabullardan keyin mijozlar sharh qoldira oladi." />
            </div>
          )}
          {list.map((r) => (
            <article key={r.id} className="card p-5">
              <div className="flex items-start gap-3">
                <Avatar name={r.is_anonymous ? '?' : r.author} size={44} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-bold text-ink-900">{r.is_anonymous || !r.author ? 'Anonim mijoz' : r.author}</span>
                    <span className="text-xs text-ink-400">{dateLong(r.created_at)}</span>
                  </div>
                  <Stars value={r.rating} size={14} />
                  {r.comment && <p className="mt-2 text-[15px] leading-relaxed text-ink-700">{r.comment}</p>}

                  {r.doctor_reply ? (
                    <div className="mt-3 rounded-2xl border-l-4 border-brand-400 bg-brand-50/60 p-3 text-sm">
                      <div className="mb-0.5 text-xs font-bold uppercase tracking-wide text-brand-700">Sizning javobingiz</div>
                      <p className="text-ink-700">{r.doctor_reply}</p>
                    </div>
                  ) : replyFor === r.id ? (
                    <div className="mt-3 space-y-2">
                      <Textarea autoFocus value={text} onChange={(e) => setText(e.target.value)} placeholder="Mijozga minnatdorchilik yoki izoh..." />
                      <div className="flex justify-end gap-2">
                        <Button size="sm" variant="ghost" onClick={() => setReplyFor(null)}>
                          Bekor
                        </Button>
                        <Button size="sm" loading={reply.isPending} disabled={!text.trim()} onClick={() => reply.mutate(r.id)}>
                          Javob berish
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <button
                      onClick={() => {
                        setReplyFor(r.id)
                        setText('')
                      }}
                      className={cx('mt-3 inline-flex items-center gap-1.5 text-sm font-semibold text-brand-700 hover:underline')}
                    >
                      <Reply className="h-4 w-4" /> Javob yozish
                    </button>
                  )}
                </div>
              </div>
            </article>
          ))}
          {q.data && list.length >= limit && (
            <div className="flex justify-center pt-2">
              <Button variant="outline" loading={q.isFetching} onClick={() => setLimit((l) => Math.min(50, l + PAGE))} disabled={limit >= 50}>
                Ko'proq ko'rsatish
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
