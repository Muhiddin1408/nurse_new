import { Navigate, Route, Routes } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Banknote, LogOut, MessageSquareWarning, Scale, Stethoscope } from 'lucide-react'
import { api } from '@/lib/api'
import { PanelLayout, type PanelNavItem } from '@/components/layout/PanelLayout'
import { Avatar, Button } from '@/components/ui'
import { AdminAuthProvider, RequireAdmin, useAdmin } from './auth'
import AdminLoginPage from './LoginPage'
import DoctorsQueue from './DoctorsQueue'
import DoctorReview from './DoctorReview'
import Reviews from './Reviews'
import Payouts from './Payouts'
import Disputes from './Disputes'
import type { ModerationDoctor, ModReview } from './types'

function AdminMenu() {
  const { admin, logout } = useAdmin()
  return (
    <div className="flex items-center gap-3">
      <div className="hidden text-right sm:block">
        <div className="text-sm font-bold text-ink-900">{admin?.full_name || admin?.login}</div>
        <div className="text-xs text-ink-400">{admin?.login}</div>
      </div>
      <Avatar name={admin?.full_name || admin?.login || 'A'} size={36} className="rounded-xl" />
      <Button variant="ghost" size="sm" icon={<LogOut className="h-4 w-4" />} onClick={logout}>
        Chiqish
      </Button>
    </div>
  )
}

function AdminPanel() {
  // Navigatsiyadagi badge'lar: kutilayotgan ishlar soni
  const pendingDoctors = useQuery({
    queryKey: ['admin', 'doctors', 'pending'],
    queryFn: () => api<ModerationDoctor[]>('/admin/moderation/doctors', { query: { status: 'pending' }, realm: 'admin' }),
  })
  const pendingReviews = useQuery({
    queryKey: ['admin', 'reviews', 'pending'],
    queryFn: () => api<ModReview[]>('/admin/reviews', { query: { status: 'pending', limit: 100 }, realm: 'admin' }),
  })

  const nav: PanelNavItem[] = [
    { to: '/admin', label: 'Shifokorlar', icon: Stethoscope, end: true, badge: pendingDoctors.data?.length },
    { to: '/admin/reviews', label: 'Sharhlar', icon: MessageSquareWarning, badge: pendingReviews.data?.length },
    { to: '/admin/payouts', label: "To'lovlar", icon: Banknote },
    { to: '/admin/disputes', label: 'Nizolar', icon: Scale },
  ]

  return (
    <PanelLayout title="Moderatsiya" nav={nav} menu={<AdminMenu />}>
      <Routes>
        <Route index element={<DoctorsQueue />} />
        <Route path="doctors/:id" element={<DoctorReview />} />
        <Route path="reviews" element={<Reviews />} />
        <Route path="payouts" element={<Payouts />} />
        <Route path="disputes" element={<Disputes />} />
        <Route path="*" element={<Navigate to="/admin" replace />} />
      </Routes>
    </PanelLayout>
  )
}

/** Platforma admini — mijoz sessiyasidan alohida (admin-service JWT, ADR-0005). */
export default function AdminApp() {
  return (
    <AdminAuthProvider>
      <Routes>
        <Route path="login" element={<AdminLoginPage />} />
        <Route
          path="*"
          element={
            <RequireAdmin>
              <AdminPanel />
            </RequireAdmin>
          }
        />
      </Routes>
    </AdminAuthProvider>
  )
}
