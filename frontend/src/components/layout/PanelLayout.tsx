import { useEffect, useState, type ComponentType, type ReactNode } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Menu, X } from 'lucide-react'
import { Logo } from '../Logo'
import { cx } from '../ui'
import { UserMenu } from './UserMenu'

export interface PanelNavItem {
  to: string
  label: string
  icon: ComponentType<{ className?: string }>
  end?: boolean
  badge?: number
}

/** Shifokor kabineti, klinika paneli va moderatsiya uchun umumiy maket. */
export function PanelLayout({ title, nav, banner, children }: { title: string; nav: PanelNavItem[]; banner?: ReactNode; children?: ReactNode }) {
  const [open, setOpen] = useState(false)
  const loc = useLocation()
  useEffect(() => setOpen(false), [loc.pathname])

  const sidebar = (
    <div className="flex h-full flex-col">
      <div className="flex h-[72px] items-center px-5">
        <Logo light to={nav[0]?.to ?? '/'} />
      </div>
      <div className="px-5 pb-3 text-[11px] font-bold uppercase tracking-[0.16em] text-white/40">{title}</div>
      <nav className="flex-1 space-y-1 overflow-y-auto px-3">
        {nav.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            end={n.end}
            className={({ isActive }) =>
              cx(
                'group flex items-center gap-3 rounded-2xl px-3 py-2.5 text-[15px] font-semibold transition',
                isActive ? 'bg-white/10 text-white shadow-[inset_0_0_0_1px_rgba(255,255,255,.06)]' : 'text-white/60 hover:bg-white/5 hover:text-white',
              )
            }
          >
            {({ isActive }) => (
              <>
                <span className={cx('flex h-9 w-9 items-center justify-center rounded-xl transition', isActive ? 'bg-gradient-to-br from-brand-400 to-brand-600 text-white shadow-glow' : 'bg-white/5 text-white/60 group-hover:text-white')}>
                  <n.icon className="h-[18px] w-[18px]" />
                </span>
                <span className="flex-1">{n.label}</span>
                {!!n.badge && <span className="rounded-full bg-rose-500 px-2 py-0.5 text-[11px] font-bold text-white">{n.badge}</span>}
              </>
            )}
          </NavLink>
        ))}
      </nav>
      <div className="m-3 rounded-3xl bg-gradient-to-br from-brand-500/20 to-sky-500/10 p-4 text-sm text-white/70 ring-1 ring-white/10">
        <div className="font-bold text-white">Yordam kerakmi?</div>
        <div className="mt-1 text-xs">Qo'llab-quvvatlash: +998 71 200-01-01</div>
      </div>
    </div>
  )

  return (
    <div className="min-h-screen bg-ink-50">
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-72 bg-ink-950 lg:block">{sidebar}</aside>
      <AnimatePresence>
        {open && (
          <div className="fixed inset-0 z-50 lg:hidden">
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="absolute inset-0 bg-ink-950/60 backdrop-blur-sm" onClick={() => setOpen(false)} />
            <motion.aside initial={{ x: -300 }} animate={{ x: 0 }} exit={{ x: -300 }} transition={{ type: 'spring', bounce: 0, duration: 0.35 }} className="absolute inset-y-0 left-0 w-72 bg-ink-950">
              <button onClick={() => setOpen(false)} className="absolute right-3 top-5 rounded-xl p-2 text-white/60 hover:bg-white/10">
                <X className="h-5 w-5" />
              </button>
              {sidebar}
            </motion.aside>
          </div>
        )}
      </AnimatePresence>
      <div className="lg:pl-72">
        <header className="sticky top-0 z-30 border-b border-ink-100 bg-white/80 backdrop-blur-xl">
          <div className="flex h-16 items-center justify-between gap-3 px-4 sm:px-6 lg:px-8">
            <button className="rounded-xl p-2 text-ink-700 hover:bg-ink-100 lg:hidden" onClick={() => setOpen(true)} aria-label="Menyu">
              <Menu className="h-6 w-6" />
            </button>
            <div className="hidden text-sm font-semibold text-ink-500 lg:block">{title}</div>
            <UserMenu />
          </div>
        </header>
        {banner}
        <main className="px-4 py-6 sm:px-6 lg:px-8 lg:py-8">{children ?? <Outlet />}</main>
      </div>
    </div>
  )
}
