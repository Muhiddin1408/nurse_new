import { Navigate, Route, Routes } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Building2, CalendarDays, ClipboardList, LayoutDashboard, MessageSquareQuote, Stethoscope, UserRound, Wallet } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { useToast } from '@/lib/toast'
import { PanelLayout, type PanelNavItem } from '@/components/layout/PanelLayout'
import { Button, ErrorState, Spinner } from '@/components/ui'
import AppointmentsPage from './AppointmentsPage'
import ClinicsPage, { useInvites } from './ClinicsPage'
import EarningsPage from './EarningsPage'
import { OnboardingShell, OnboardingWizard, StatusScreen, SuspendedBanner } from './Onboarding'
import ProfilePage from './ProfilePage'
import ReviewsPage from './ReviewsPage'
import SchedulePage from './SchedulePage'
import ServicesPage from './ServicesPage'
import TodayPage from './TodayPage'
import type { OnboardingStatus } from './shared'

export default function DoctorApp() {
  const status = useQuery({
    queryKey: ['doctor', 'onboarding-status'],
    queryFn: () => api<OnboardingStatus>('/doctor/onboarding/status'),
    retry: false,
  })

  if (status.isLoading)
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner className="h-8 w-8" />
      </div>
    )

  if (status.isError) {
    // Shifokor profili hali yaratilmagan — anketani boshlash taklifi
    if (status.error instanceof ApiError && status.error.status === 403) return <StartOnboarding />
    return (
      <OnboardingShell>
        <ErrorState error={status.error} onRetry={() => status.refetch()} />
      </OnboardingShell>
    )
  }

  const s = status.data!
  if (s.status === 'approved') return <ApprovedCabinet />
  if (s.status === 'pending') return <StatusScreen status={s} />
  if (s.status === 'suspended')
    return (
      <PanelLayout title="Shifokor kabineti" nav={[{ to: '/doctor', label: 'Moliya', icon: Wallet, end: true }]} banner={<SuspendedBanner reason={s.reason} />}>
        <EarningsPage />
      </PanelLayout>
    )
  return <OnboardingWizard status={s} />
}

function ApprovedCabinet() {
  const invites = useInvites()
  const nav: PanelNavItem[] = [
    { to: '/doctor', label: 'Bugun', icon: LayoutDashboard, end: true },
    { to: '/doctor/appointments', label: 'Qabullar', icon: ClipboardList },
    { to: '/doctor/schedule', label: 'Jadval', icon: CalendarDays },
    { to: '/doctor/services', label: 'Xizmatlar', icon: Stethoscope },
    { to: '/doctor/earnings', label: 'Moliya', icon: Wallet },
    { to: '/doctor/reviews', label: 'Sharhlar', icon: MessageSquareQuote },
    { to: '/doctor/clinics', label: 'Klinikalar', icon: Building2, badge: invites.data?.length },
    { to: '/doctor/profile', label: 'Profil', icon: UserRound },
  ]
  return (
    <Routes>
      <Route element={<PanelLayout title="Shifokor kabineti" nav={nav} />}>
        <Route index element={<TodayPage />} />
        <Route path="appointments" element={<AppointmentsPage />} />
        <Route path="schedule" element={<SchedulePage />} />
        <Route path="services" element={<ServicesPage />} />
        <Route path="earnings" element={<EarningsPage />} />
        <Route path="reviews" element={<ReviewsPage />} />
        <Route path="clinics" element={<ClinicsPage />} />
        <Route path="profile" element={<ProfilePage />} />
        <Route path="*" element={<Navigate to="/doctor" replace />} />
      </Route>
    </Routes>
  )
}

function StartOnboarding() {
  const qc = useQueryClient()
  const toast = useToast()
  const start = useMutation({
    mutationFn: () => api('/doctor/onboarding/start', { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['doctor'] }),
    onError: (e) => toast.error(e),
  })
  return (
    <OnboardingShell>
      <div className="card mx-auto max-w-lg p-8 text-center">
        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-3xl bg-gradient-to-br from-brand-400 to-brand-600 text-white shadow-glow">
          <Stethoscope className="h-8 w-8" />
        </div>
        <h1 className="text-2xl font-extrabold">Shifokor anketasini boshlang</h1>
        <p className="mt-2 text-ink-500">Anketani to'ldiring, hujjatlarni yuklang va moderatsiyadan o'tgach mijozlar sizga yozila boshlaydi.</p>
        <Button className="mt-6" size="lg" loading={start.isPending} onClick={() => start.mutate()}>
          Boshlash
        </Button>
      </div>
    </OnboardingShell>
  )
}
