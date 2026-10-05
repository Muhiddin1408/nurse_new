import { NavLink, Route, Routes } from 'react-router-dom'
import { CalendarCheck2, Heart, LayoutDashboard, MapPin, Package, Settings, Timer, Users } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { phone } from '@/lib/format'
import { Avatar, cx } from '@/components/ui'
import Overview from './Overview'
import BookingsPage from './BookingsPage'
import BookingDetail from './BookingDetail'
import PatientsPage from './PatientsPage'
import AddressesPage from './AddressesPage'
import FavoritesPage from './FavoritesPage'
import PackagesPage from './PackagesPage'
import WaitlistPage from './WaitlistPage'
import SettingsPage from './SettingsPage'

const NAV = [
  { to: '/account', label: 'Umumiy', icon: LayoutDashboard, end: true },
  { to: '/account/bookings', label: 'Bronlarim', icon: CalendarCheck2 },
  { to: '/account/patients', label: 'Bemorlar', icon: Users },
  { to: '/account/addresses', label: 'Manzillar', icon: MapPin },
  { to: '/account/favorites', label: 'Sevimlilar', icon: Heart },
  { to: '/account/packages', label: 'Paketlarim', icon: Package },
  { to: '/account/waitlist', label: 'Navbatlar', icon: Timer },
  { to: '/account/settings', label: 'Sozlamalar', icon: Settings },
]

export default function AccountApp() {
  const { user } = useAuth()
  return (
    <div className="container-x py-6 lg:py-10">
      <div className="grid gap-8 lg:grid-cols-[260px_1fr]">
        <aside className="hidden lg:block">
          <div className="card sticky top-24 p-3">
            <div className="flex items-center gap-3 p-3">
              <Avatar name={user?.full_name || user?.phone} size={44} className="rounded-xl" />
              <div className="min-w-0">
                <div className="truncate font-bold text-ink-900">{user?.full_name || 'Foydalanuvchi'}</div>
                <div className="text-xs text-ink-500">{phone(user?.phone)}</div>
              </div>
            </div>
            <nav className="mt-2 space-y-0.5">
              {NAV.map((n) => (
                <NavLink
                  key={n.to}
                  to={n.to}
                  end={n.end}
                  className={({ isActive }) => cx('flex items-center gap-3 rounded-2xl px-3 py-2.5 text-[15px] font-semibold transition', isActive ? 'bg-brand-50 text-brand-700' : 'text-ink-600 hover:bg-ink-50 hover:text-ink-900')}
                >
                  <n.icon className="h-[18px] w-[18px]" />
                  {n.label}
                </NavLink>
              ))}
            </nav>
          </div>
        </aside>
        <div className="min-w-0">
          <div className="-mx-4 mb-6 flex gap-2 overflow-x-auto px-4 pb-1 scrollbar-none lg:hidden">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.end}
                className={({ isActive }) => cx('flex shrink-0 items-center gap-1.5 rounded-full px-3.5 py-2 text-sm font-semibold', isActive ? 'bg-ink-900 text-white' : 'bg-white text-ink-600 ring-1 ring-ink-100')}
              >
                <n.icon className="h-4 w-4" />
                {n.label}
              </NavLink>
            ))}
          </div>
          <Routes>
            <Route index element={<Overview />} />
            <Route path="bookings" element={<BookingsPage />} />
            <Route path="bookings/:id" element={<BookingDetail />} />
            <Route path="patients" element={<PatientsPage />} />
            <Route path="addresses" element={<AddressesPage />} />
            <Route path="favorites" element={<FavoritesPage />} />
            <Route path="packages" element={<PackagesPage />} />
            <Route path="waitlist" element={<WaitlistPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Routes>
        </div>
      </div>
    </div>
  )
}
