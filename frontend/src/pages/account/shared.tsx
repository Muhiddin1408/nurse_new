import { Link } from 'react-router-dom'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Building2, ChevronRight, House, UserRound } from 'lucide-react'
import { api } from '@/lib/api'
import { dayNum, dateShort, money, time, weekdayShort } from '@/lib/format'
import type { Booking } from '@/lib/types'
import { StatusBadge, cx } from '@/components/ui'

export const ACTIVE_STATUSES = 'pending_payment,confirmed'
export const HISTORY_STATUSES = 'completed,cancelled,expired,no_show'

interface Page {
  next: string | null
  results: Booking[]
}

export function useMyBookings(status: string, pageSize = 20) {
  return useInfiniteQuery({
    queryKey: ['bookings', 'mine', status, pageSize],
    initialPageParam: '',
    queryFn: ({ pageParam }) => api<Page>('/booking/bookings/mine', { query: { status, page_size: pageSize, cursor: pageParam || undefined } }),
    // Cursor paginatsiya: `next` URL ichidagi `cursor` qiymati
    getNextPageParam: (last) => (last.next ? new URL(last.next, location.origin).searchParams.get('cursor') ?? undefined : undefined),
  })
}

export function BookingCard({ b }: { b: Booking }) {
  const start = b.start_at
  const pending = b.status === 'pending_payment'
  return (
    <Link to={`/account/bookings/${b.id}`} className={cx('card group flex items-stretch gap-4 p-4 transition hover:shadow-lift sm:p-5', pending && 'ring-2 ring-amber-200')}>
      {start && (
        <div className={cx('flex w-16 shrink-0 flex-col items-center justify-center rounded-2xl py-2 sm:w-20', b.status === 'confirmed' || pending ? 'bg-gradient-to-br from-brand-500 to-brand-700 text-white' : 'bg-ink-100 text-ink-600')}>
          <span className="text-[11px] font-semibold uppercase opacity-70">{weekdayShort(start)}</span>
          <span className="text-2xl font-extrabold leading-tight">{dayNum(start)}</span>
          <span className="text-[11px] font-semibold opacity-80">{dateShort(start).split(' ')[1]}</span>
        </div>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={b.status} />
          <span className="text-xs font-semibold text-ink-400">№ {b.number}</span>
        </div>
        <div className="mt-1.5 truncate font-bold text-ink-900">{b.doctor_name ?? 'Shifokor'}</div>
        <div className="truncate text-sm text-ink-500">{b.items.map((i) => i.service_name).join(', ')}</div>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-500">
          {start && (
            <span className="font-semibold text-ink-700">
              {time(start)}
              {b.end_at && `–${time(b.end_at)}`}
            </span>
          )}
          <span className="inline-flex items-center gap-1">
            {b.place === 'home' ? <House className="h-3.5 w-3.5" /> : <Building2 className="h-3.5 w-3.5" />}
            <span className="max-w-[180px] truncate">{b.place === 'home' ? 'Uyga chaqiruv' : b.clinic_name || 'Klinikada'}</span>
          </span>
          {b.patient_name && (
            <span className="inline-flex items-center gap-1">
              <UserRound className="h-3.5 w-3.5" /> {b.patient_name}
            </span>
          )}
        </div>
      </div>
      <div className="flex flex-col items-end justify-between">
        <span className="whitespace-nowrap font-bold text-ink-900">{money(b.total_price)}</span>
        <ChevronRight className="h-5 w-5 text-ink-300 transition group-hover:translate-x-0.5 group-hover:text-brand-600" />
      </div>
    </Link>
  )
}
