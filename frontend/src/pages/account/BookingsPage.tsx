import { useState } from 'react'
import { Link } from 'react-router-dom'
import { CalendarPlus, History } from 'lucide-react'
import { Button, EmptyState, ErrorState, ListSkeleton, PageHeader, Segmented } from '@/components/ui'
import { ACTIVE_STATUSES, BookingCard, HISTORY_STATUSES, useMyBookings } from './shared'

export default function BookingsPage() {
  const [tab, setTab] = useState<'active' | 'history'>('active')
  const q = useMyBookings(tab === 'active' ? ACTIVE_STATUSES : HISTORY_STATUSES)
  const items = q.data?.pages.flatMap((p) => p.results) ?? []

  return (
    <div>
      <PageHeader
        title="Bronlarim"
        subtitle="Barcha qabullaringiz bir joyda"
        actions={
          <Link to="/doctors">
            <Button icon={<CalendarPlus className="h-4 w-4" />}>Yangi bron</Button>
          </Link>
        }
      />
      <Segmented
        className="mb-5"
        value={tab}
        onChange={setTab}
        options={[
          { value: 'active', label: 'Faol' },
          { value: 'history', label: 'Tarix' },
        ]}
      />
      {q.isError ? (
        <div className="card">
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        </div>
      ) : q.isLoading ? (
        <ListSkeleton />
      ) : items.length === 0 ? (
        <div className="card">
          {tab === 'active' ? (
            <EmptyState
              icon={<CalendarPlus className="h-7 w-7" />}
              title="Faol bronlar yo'q"
              text="Shifokorni tanlang va qulay vaqtga yoziling — bu 2 daqiqa oladi."
              action={
                <Link to="/doctors">
                  <Button>Shifokor topish</Button>
                </Link>
              }
            />
          ) : (
            <EmptyState icon={<History className="h-7 w-7" />} title="Tarix bo'sh" text="Yakunlangan va bekor qilingan bronlar shu yerda ko'rinadi." />
          )}
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((b) => (
            <BookingCard key={b.id} b={b} />
          ))}
          {q.hasNextPage && (
            <Button variant="outline" block loading={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>
              Yana yuklash
            </Button>
          )}
        </div>
      )}
    </div>
  )
}
