import { lazy, Suspense, type ReactNode } from 'react'
import { Route, Routes } from 'react-router-dom'
import { PublicLayout } from '@/components/layout/PublicLayout'
import { RequireAuth } from '@/components/RequireAuth'
import { Spinner } from '@/components/ui'
import HomePage from '@/pages/client/HomePage'

const DoctorsPage = lazy(() => import('@/pages/client/DoctorsPage'))
const DoctorPage = lazy(() => import('@/pages/client/DoctorPage'))
const ClinicsPage = lazy(() => import('@/pages/client/ClinicsPage'))
const BookingWizard = lazy(() => import('@/pages/client/BookingWizard'))
const LoginPage = lazy(() => import('@/pages/client/LoginPage'))
const ForDoctorsPage = lazy(() => import('@/pages/client/ForDoctorsPage'))
const NotFound = lazy(() => import('@/pages/client/NotFound'))
const AccountApp = lazy(() => import('@/pages/account/AccountApp'))
const DoctorApp = lazy(() => import('@/pages/doctor/DoctorApp'))
const ClinicApp = lazy(() => import('@/pages/clinic/ClinicApp'))
const AdminApp = lazy(() => import('@/pages/admin/AdminApp'))

function Lazy({ children }: { children: ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-[50vh] items-center justify-center">
          <Spinner className="h-8 w-8" />
        </div>
      }
    >
      {children}
    </Suspense>
  )
}

export default function App() {
  return (
    <Lazy>
      <Routes>
        <Route element={<PublicLayout />}>
          <Route index element={<HomePage />} />
          <Route path="doctors" element={<Lazy><DoctorsPage /></Lazy>} />
          <Route path="doctors/:id" element={<Lazy><DoctorPage /></Lazy>} />
          <Route path="clinics" element={<Lazy><ClinicsPage /></Lazy>} />
          <Route path="for-doctors" element={<Lazy><ForDoctorsPage /></Lazy>} />
          <Route path="login" element={<Lazy><LoginPage /></Lazy>} />
          <Route
            path="book/:doctorId"
            element={
              <RequireAuth role="client">
                <Lazy><BookingWizard /></Lazy>
              </RequireAuth>
            }
          />
          <Route
            path="account/*"
            element={
              <RequireAuth role="client">
                <Lazy><AccountApp /></Lazy>
              </RequireAuth>
            }
          />
          <Route path="*" element={<Lazy><NotFound /></Lazy>} />
        </Route>
        <Route
          path="doctor/*"
          element={
            <RequireAuth role="doctor">
              <Lazy><DoctorApp /></Lazy>
            </RequireAuth>
          }
        />
        <Route
          path="clinic/*"
          element={
            <RequireAuth role="clinic_admin">
              <Lazy><ClinicApp /></Lazy>
            </RequireAuth>
          }
        />
        <Route
          path="admin/*"
          element={
            <RequireAuth role="platform_admin">
              <Lazy><AdminApp /></Lazy>
            </RequireAuth>
          }
        />
      </Routes>
    </Lazy>
  )
}
