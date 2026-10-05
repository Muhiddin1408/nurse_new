import { Navigate, Route, Routes } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Banknote, MessageSquareWarning, Scale, Stethoscope } from 'lucide-react'
import { api } from '@/lib/api'
import { PanelLayout, type PanelNavItem } from '@/components/layout/PanelLayout'
import DoctorsQueue from './DoctorsQueue'
import DoctorReview from './DoctorReview'
import Reviews from './Reviews'
import Payouts from './Payouts'
import Disputes from './Disputes'
import type { ModerationDoctor, ModReview } from './types'

export default function AdminApp() {
  // Navigatsiyadagi badge'lar: kutilayotgan ishlar soni
  const pendingDoctors = useQuery({ queryKey: ['mod', 'doctors', 'pending'], queryFn: () => api<ModerationDoctor[]>('/moderation/doctors', { query: { status: 'pending' } }) })
  const pendingReviews = useQuery({ queryKey: ['mod', 'reviews'], queryFn: () => api<ModReview[]>('/moderation/reviews', { query: { limit: 50 } }) })

  const nav: PanelNavItem[] = [
    { to: '/admin', label: 'Shifokorlar', icon: Stethoscope, end: true, badge: pendingDoctors.data?.length },
    { to: '/admin/reviews', label: 'Sharhlar', icon: MessageSquareWarning, badge: pendingReviews.data?.length },
    { to: '/admin/payouts', label: "To'lovlar", icon: Banknote },
    { to: '/admin/disputes', label: 'Nizolar', icon: Scale },
  ]

  return (
    <PanelLayout title="Moderatsiya" nav={nav}>
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
