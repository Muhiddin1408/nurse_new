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
import { ApiError } from '@/lib/api'
import { PanelLayout, type PanelNavItem } from '@/components/layout/PanelLayout'
import { Button, ErrorState, Field, Input, Modal, Skeleton, Spinner } from '@/components/ui'
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

function ClinicSwitcherModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { clinicId, setClinicId } = useClinic()
  const [val, setVal] = useState(clinicId)
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Klinikani tanlash"
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={() => { setClinicId(''); onClose() }}>
            Tozalash
          </Button>
          <Button onClick={() => { setClinicId(val.trim()); onClose() }} disabled={!val.trim()}>
            Saqlash
          </Button>
        </>
      }
    >
      <ClinicIdForm value={val} onChange={setVal} />
    </Modal>
  )
}

function ClinicIdForm({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="space-y-3">
      <p className="text-sm leading-relaxed text-ink-500">
        Siz bir nechta klinikani boshqarasiz. Ishlamoqchi bo'lgan klinika identifikatorini (UUID) kiriting — uni platforma administratoridan olishingiz mumkin.
      </p>
      <Field label="Klinika ID">
        <Input value={value} onChange={(e) => onChange(e.target.value)} placeholder="4f0414e9-9bd8-4786-..." className="font-mono text-sm" />
      </Field>
    </div>
  )
}

/** Klinika aniqlanmaguncha (409/404) panelni ochmaydi. */
function ClinicGate({ children }: { children: ReactNode }) {
  const { clinicId, setClinicId } = useClinic()
  const q = useClinicProfile()
  const [val, setVal] = useState('')

  if (q.isLoading)
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

  if (q.error instanceof ApiError && (q.error.status === 409 || q.error.status === 404)) {
    const notFound = q.error.status === 404
    return (
      <div className="mx-auto mt-10 max-w-md">
        <div className="card p-7">
          <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-50 to-sky-50 text-brand-600 ring-1 ring-brand-100">
            <Building2 className="h-7 w-7" />
          </div>
          <h2 className="text-xl font-bold">{notFound ? 'Klinika topilmadi' : 'Klinikani tanlang'}</h2>
          {notFound && clinicId && <p className="mt-1 text-sm text-rose-600">Saqlangan ID sizning klinikalaringizga tegishli emas.</p>}
          <div className="mt-4">
            <ClinicIdForm value={val} onChange={setVal} />
          </div>
          <div className="mt-5 flex gap-2">
            {clinicId && (
              <Button variant="outline" onClick={() => setClinicId('')}>
                Avtomatik
              </Button>
            )}
            <Button block onClick={() => setClinicId(val.trim())} disabled={!val.trim()}>
              Davom etish
            </Button>
          </div>
        </div>
      </div>
    )
  }

  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  return <>{children}</>
}

function ClinicBanner() {
  const q = useClinicProfile()
  const { clinicId } = useClinic()
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
      {(clinicId || q.isFetching) && (
        <Button variant="ghost" size="sm" onClick={() => setOpen(true)} icon={q.isFetching ? <Spinner className="h-4 w-4" /> : <ArrowLeftRight className="h-4 w-4" />}>
          Almashtirish
        </Button>
      )}
      <ClinicSwitcherModal open={open} onClose={() => setOpen(false)} />
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
