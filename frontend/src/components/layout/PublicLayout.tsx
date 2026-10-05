import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { CalendarCheck2, Home, Mail, MapPin, Menu, Phone, Search, Send, User, X } from 'lucide-react'
import { AnimatePresence, motion } from 'framer-motion'
import { useAuth } from '@/lib/auth'
import { Logo } from '../Logo'
import { Button, cx } from '../ui'
import { UserMenu } from './UserMenu'

const NAV = [
  { to: '/doctors', label: 'Shifokorlar' },
  { to: '/clinics', label: 'Klinikalar' },
  { to: '/doctors?home=1', label: 'Uyga chaqiruv' },
  { to: '/#how', label: 'Qanday ishlaydi' },
]

export function PublicLayout() {
  const { isAuthed, user } = useAuth()
  const loc = useLocation()
  const [scrolled, setScrolled] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 8)
    on()
    window.addEventListener('scroll', on, { passive: true })
    return () => window.removeEventListener('scroll', on)
  }, [])

  useEffect(() => {
    setMobileOpen(false)
    if (!loc.hash) window.scrollTo({ top: 0 })
    else document.getElementById(loc.hash.slice(1))?.scrollIntoView({ behavior: 'smooth' })
  }, [loc.pathname, loc.hash])

  const hideTabs = loc.pathname.startsWith('/book/') || loc.pathname === '/login'

  return (
    <div className="flex min-h-screen flex-col">
      <header className={cx('sticky top-0 z-40 transition-all duration-300', scrolled ? 'bg-white/80 shadow-[0_1px_0_rgba(16,24,40,.06)] backdrop-blur-xl' : 'bg-transparent')}>
        <div className="container-x flex h-[72px] items-center justify-between gap-4">
          <Logo />
          <nav className="hidden items-center gap-1 lg:flex">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                className={({ isActive }) =>
                  cx('rounded-xl px-4 py-2 text-[15px] font-semibold transition', isActive && !n.to.includes('?') && !n.to.includes('#') ? 'text-brand-700' : 'text-ink-600 hover:text-ink-900')
                }
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
          <div className="flex items-center gap-2">
            {isAuthed ? (
              <>
                {user?.active_role === 'client' && (
                  <Link to="/account/bookings" className="hidden items-center gap-2 rounded-xl px-3 py-2 text-sm font-semibold text-ink-600 hover:bg-ink-100 md:flex">
                    <CalendarCheck2 className="h-4 w-4" /> Bronlarim
                  </Link>
                )}
                <UserMenu />
              </>
            ) : (
              <>
                <Link to="/login" className="hidden sm:block">
                  <Button variant="ghost">Kirish</Button>
                </Link>
                <Link to="/doctors">
                  <Button size="md" className="hidden sm:inline-flex">
                    Qabulga yozilish
                  </Button>
                </Link>
              </>
            )}
            <button className="rounded-xl p-2 text-ink-700 hover:bg-ink-100 lg:hidden" onClick={() => setMobileOpen((o) => !o)} aria-label="Menyu">
              {mobileOpen ? <X className="h-6 w-6" /> : <Menu className="h-6 w-6" />}
            </button>
          </div>
        </div>
        <AnimatePresence>
          {mobileOpen && (
            <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden border-t border-ink-100 bg-white lg:hidden">
              <div className="container-x flex flex-col gap-1 py-3">
                {NAV.map((n) => (
                  <Link key={n.to} to={n.to} className="rounded-xl px-3 py-3 font-semibold text-ink-700 hover:bg-ink-50">
                    {n.label}
                  </Link>
                ))}
                {!isAuthed && (
                  <Link to="/login" className="mt-2">
                    <Button block>Kirish / Ro'yxatdan o'tish</Button>
                  </Link>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </header>

      <main className={cx('flex-1', !hideTabs && 'pb-20 md:pb-0')}>
        <Outlet />
      </main>

      <Footer />
      {!hideTabs && <MobileTabBar />}
    </div>
  )
}

function MobileTabBar() {
  const { isAuthed } = useAuth()
  const items = [
    { to: '/', label: 'Bosh sahifa', icon: Home, end: true },
    { to: '/doctors', label: 'Qidiruv', icon: Search },
    { to: isAuthed ? '/account/bookings' : '/login', label: 'Bronlarim', icon: CalendarCheck2 },
    { to: isAuthed ? '/account' : '/login', label: 'Profil', icon: User, end: true },
  ]
  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-ink-100 bg-white/90 pb-[env(safe-area-inset-bottom)] backdrop-blur-xl md:hidden">
      <div className="grid grid-cols-4">
        {items.map((i) => (
          <NavLink key={i.label} to={i.to} end={i.end} className={({ isActive }) => cx('flex flex-col items-center gap-1 py-2.5 text-[11px] font-semibold', isActive ? 'text-brand-600' : 'text-ink-400')}>
            <i.icon className="h-5 w-5" />
            {i.label}
          </NavLink>
        ))}
      </div>
    </nav>
  )
}

function Footer() {
  return (
    <footer className="relative mt-20 overflow-hidden bg-ink-950 text-ink-300">
      <div className="pointer-events-none absolute -top-40 left-1/2 h-80 w-[60rem] -translate-x-1/2 rounded-full bg-brand-500/10 blur-3xl" />
      <div className="container-x relative grid gap-10 py-14 md:grid-cols-2 lg:grid-cols-4">
        <div>
          <Logo light />
          <p className="mt-4 max-w-xs text-sm leading-relaxed text-ink-400">
            Toshkentdagi tasdiqlangan shifokorlarga onlayn yoziling — klinikada yoki uyingizda. Navbatsiz, qo'ng'iroqsiz, 2 daqiqada.
          </p>
        </div>
        <div>
          <h4 className="mb-4 text-sm font-bold uppercase tracking-wider text-white">Bemorlarga</h4>
          <ul className="space-y-2.5 text-sm">
            <li><Link className="hover:text-white" to="/doctors">Shifokor topish</Link></li>
            <li><Link className="hover:text-white" to="/clinics">Klinikalar</Link></li>
            <li><Link className="hover:text-white" to="/doctors?home=1">Uyga chaqiruv</Link></li>
            <li><Link className="hover:text-white" to="/account/bookings">Mening bronlarim</Link></li>
          </ul>
        </div>
        <div>
          <h4 className="mb-4 text-sm font-bold uppercase tracking-wider text-white">Hamkorlarga</h4>
          <ul className="space-y-2.5 text-sm">
            <li><Link className="hover:text-white" to="/for-doctors">Shifokor bo'lib qo'shilish</Link></li>
            <li><Link className="hover:text-white" to="/login">Klinika paneli</Link></li>
            <li><Link className="hover:text-white" to="/login">Shifokor kabineti</Link></li>
          </ul>
        </div>
        <div>
          <h4 className="mb-4 text-sm font-bold uppercase tracking-wider text-white">Aloqa</h4>
          <ul className="space-y-3 text-sm">
            <li className="flex items-center gap-2.5"><Phone className="h-4 w-4 text-brand-400" /> +998 71 200-01-01</li>
            <li className="flex items-center gap-2.5"><Mail className="h-4 w-4 text-brand-400" /> info@turonclinic.uz</li>
            <li className="flex items-center gap-2.5"><MapPin className="h-4 w-4 text-brand-400" /> Toshkent, Chilonzor tumani</li>
            <li className="flex items-center gap-2.5"><Send className="h-4 w-4 text-brand-400" /> @turonclinic_bot</li>
          </ul>
        </div>
      </div>
      <div className="border-t border-white/5">
        <div className="container-x flex flex-col items-center justify-between gap-2 py-5 text-xs text-ink-500 sm:flex-row">
          <span>© {new Date().getFullYear()} Turon Clinic. Barcha huquqlar himoyalangan.</span>
          <span>Shoshilinch holatda: <b className="text-ink-300">103</b></span>
        </div>
      </div>
    </footer>
  )
}
