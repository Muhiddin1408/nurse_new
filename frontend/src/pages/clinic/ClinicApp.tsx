import { useState, type ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import {
  BarChart3,
  Building2,
  CalendarClock,
  CalendarOff,
  ClipboardList,
  DoorOpen,
  LayoutDashboard,
  ListChecks,
  Stethoscope,
  Tags,
  ArrowLeftRight,
} from 'lucide-react'
import { PanelLayout, type PanelNavItem } from '@/components/layout/PanelLayout'
import { Badge, Button, ErrorState, Modal, Skeleton, Spinner, cx } from '@/components/ui'
import { ClinicProvider, useClinic, useClinicProfile } from './ctx'
import Dashboard from './Dashboard'
import Appointments from './Appointments'
import Schedule from './Schedule'
import Doctors from './Doctors'
import Rooms from './Rooms'
import Rules from './Rules'
import Services from './Services'
import Closures from './Closures'
import Reports from './Reports'
import Profile from './Profile'

const NAV: PanelNavItem[] = [
  { to: '/clinic', label: 'Boshqaruv', icon: LayoutDashboard, end: true },
  { to: '/clinic/appointments', label: 'Qabullar', icon: ClipboardList },
  { to: '/clinic/schedule', label: 'Jadval', icon: CalendarClock },
  { to: '/clinic/doctors', label: 'Shifokorlar', icon: Stethoscope },
  { to: '/clinic/rooms', label: 'Xonalar', icon: DoorOpen },
  { to: '/clinic/rules', label: 'Ish qoidalari', icon: ListChecks },
  { to: '/clinic/services', label: 'Xizmatlar', icon: Tags },
  { to: '/clinic/closures', label: 'Dam olish kunlari', icon: CalendarOff },
  { to: '/clinic/reports', label: 'Hisobotlar', icon: BarChart3 },
  { to: '/clinic/profile', label: 'Klinika profili', icon: Building2 },
]

/** Admin boshqaradigan klinikalar ro'yxati (catalog `GET /v1/clinic/mine`) — bosilgani faol bo'ladi. */
function ClinicPicker({ onPicked }: { onPicked?: () => void }) {
  const { clinicId, setClinicId, mine } = useClinic()
  return (
    <ul className="space-y-2">
      {(mine ?? []).map((c) => (
        <li key={c.id}>
          <button
            type="button"
            onClick={() => { setClinicId(c.id); onPicked?.() }}
            className={cx(
              'flex w-full items-center gap-3 rounded-2xl p-3 text-left ring-1 transition hover:bg-ink-50',
              c.id === clinicId ? 'bg-brand-50/60 ring-brand-300' : 'ring-ink-100',
            )}
          >
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-50 text-brand-600">
              <Building2 className="h-5 w-5" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate font-semibold text-ink-900">{c.name}</span>
              <span className="block truncate text-sm text-ink-500">{c.city}, {c.street}</span>
            </span>
            {c.status !== 'active' && <Badge tone="amber">{c.status === 'draft' ? 'Qoralama' : "To'xtatilgan"}</Badge>}
          </button>
        </li>
      ))}
    </ul>
  )
}

/** Klinika aniqlanmaguncha panelni ochmaydi: ro'yxat yuklanadi → bitta bo'lsa avtomatik, bir nechta — tanlov. */
function ClinicGate({ children }: { children: ReactNode }) {
  const { clinicId, mine, mineLoading } = useClinic()
  const q = useClinicProfile()

  if (mineLoading || (clinicId && q.isLoading))
    return (
      <div className="space-y-4">
        <Skeleton className="h-36 rounded-3xl" />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-28 rounded-3xl" />
          ))}
        </div>
      </div>
    )

  if (!clinicId) {
    const none = !mine?.length
    return (
      <div className="mx-auto mt-10 max-w-md">
        <div className="card p-7">
          <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-50 to-sky-50 text-brand-600 ring-1 ring-brand-100">
            <Building2 className="h-7 w-7" />
          </div>
          <h2 className="text-xl font-bold">{none ? 'Klinika biriktirilmagan' : 'Klinikani tanlang'}</h2>
          <p className="mt-1 text-sm leading-relaxed text-ink-500">
            {none
              ? "Sizning hisobingizga hali birorta klinika biriktirilmagan. Platforma administratoriga murojaat qiling."
              : "Siz bir nechta klinikani boshqarasiz — qaysi biri bilan ishlashni tanlang."}
          </p>
          {!none && (
            <div className="mt-5">
              <ClinicPicker />
            </div>
          )}
        </div>
      </div>
    )
  }

  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  return <>{children}</>
}

function ClinicBanner() {
  const q = useClinicProfile()
  const { mine } = useClinic()
  const [open, setOpen] = useState(false)
  if (!q.data) return null
  return (
    <div className="flex items-center justify-between gap-3 border-b border-ink-100 bg-white px-4 py-2.5 sm:px-6 lg:px-8">
      <div className="flex min-w-0 items-center gap-2.5 text-sm">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
          <Building2 className="h-4 w-4" />
        </span>
        <span className="truncate font-bold text-ink-900">{q.data.name}</span>
        <span className="hidden truncate text-ink-400 sm:inline">· {q.data.city}</span>
      </div>
      {(mine?.length ?? 0) > 1 && (
        <Button variant="ghost" size="sm" onClick={() => setOpen(true)} icon={q.isFetching ? <Spinner className="h-4 w-4" /> : <ArrowLeftRight className="h-4 w-4" />}>
          Almashtirish
        </Button>
      )}
      <Modal open={open} onClose={() => setOpen(false)} title="Klinikani tanlash" size="sm">
        <ClinicPicker onPicked={() => setOpen(false)} />
      </Modal>
    </div>
  )
}

export default function ClinicApp() {
  return (
    <ClinicProvider>
      <PanelLayout title="Klinika paneli" nav={NAV} banner={<ClinicBanner />}>
        <ClinicGate>
          <Routes>
            <Route index element={<Dashboard />} />
            <Route path="appointments" element={<Appointments />} />
            <Route path="schedule" element={<Schedule />} />
            <Route path="doctors" element={<Doctors />} />
            <Route path="rooms" element={<Rooms />} />
            <Route path="rules" element={<Rules />} />
            <Route path="services" element={<Services />} />
            <Route path="closures" element={<Closures />} />
            <Route path="reports" element={<Reports />} />
            <Route path="profile" element={<Profile />} />
            <Route path="*" element={<Navigate to="/clinic" replace />} />
          </Routes>
        </ClinicGate>
      </PanelLayout>
    </ClinicProvider>
  )
}
