import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Building2, Check, ChevronDown, LogOut, ShieldCheck, Stethoscope, User as UserIcon } from 'lucide-react'
import { ROLE_HOME, ROLE_LABEL, useAuth } from '@/lib/auth'
import { useToast } from '@/lib/toast'
import { phone } from '@/lib/format'
import type { Role } from '@/lib/types'
import { Avatar, cx } from '../ui'

const ROLE_ICON: Record<Role, typeof UserIcon> = {
  client: UserIcon,
  doctor: Stethoscope,
  clinic_admin: Building2,
  platform_admin: ShieldCheck,
}

export function UserMenu({ dark, compact }: { dark?: boolean; compact?: boolean }) {
  const { user, switchRole, logout } = useAuth()
  const toast = useToast()
  const nav = useNavigate()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<Role | null>(null)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onDoc = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false)
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  if (!user) return null
  const roles = user.available_roles?.length ? user.available_roles : [user.active_role]

  const onSwitch = async (r: Role) => {
    if (r === user.active_role) {
      nav(ROLE_HOME[r])
      setOpen(false)
      return
    }
    setBusy(r)
    try {
      await switchRole(r)
      toast.success(`${ROLE_LABEL[r]} rejimiga o'tildi`)
      setOpen(false)
      nav(ROLE_HOME[r])
    } catch (e) {
      toast.error(e)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        className={cx('flex items-center gap-2.5 rounded-2xl p-1.5 pr-3 transition', dark ? 'hover:bg-white/10' : 'hover:bg-ink-100')}
      >
        <Avatar name={user.full_name || user.phone} size={36} className="rounded-xl" />
        {!compact && (
          <span className="hidden text-left leading-tight sm:block">
            <span className={cx('block max-w-[140px] truncate text-sm font-bold', dark ? 'text-white' : 'text-ink-900')}>{user.full_name || 'Foydalanuvchi'}</span>
            <span className={cx('block text-xs', dark ? 'text-white/60' : 'text-ink-500')}>{ROLE_LABEL[user.active_role]}</span>
          </span>
        )}
        <ChevronDown className={cx('h-4 w-4', dark ? 'text-white/60' : 'text-ink-400')} />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.98 }}
            transition={{ duration: 0.15 }}
            className="absolute right-0 z-50 mt-2 w-72 origin-top-right overflow-hidden rounded-3xl bg-white p-2 shadow-lift ring-1 ring-ink-100"
          >
            <div className="px-3 py-3">
              <div className="font-bold text-ink-900">{user.full_name || 'Foydalanuvchi'}</div>
              <div className="text-sm text-ink-500">{phone(user.phone)}</div>
            </div>
            <div className="px-3 pb-1 pt-2 text-[11px] font-bold uppercase tracking-wider text-ink-400">Kabinetlar</div>
            {roles.map((r) => {
              const Icon = ROLE_ICON[r]
              return (
                <button
                  key={r}
                  onClick={() => onSwitch(r)}
                  disabled={!!busy}
                  className="flex w-full items-center gap-3 rounded-2xl px-3 py-2.5 text-left text-sm font-semibold text-ink-700 transition hover:bg-ink-50"
                >
                  <span className={cx('flex h-9 w-9 items-center justify-center rounded-xl', r === user.active_role ? 'bg-brand-500 text-white' : 'bg-ink-100 text-ink-500')}>
                    <Icon className="h-4 w-4" />
                  </span>
                  <span className="flex-1">{ROLE_LABEL[r]}</span>
                  {busy === r ? <span className="h-4 w-4 animate-spin rounded-full border-2 border-brand-500 border-t-transparent" /> : r === user.active_role && <Check className="h-4 w-4 text-brand-600" />}
                </button>
              )
            })}
            {user.active_role === 'client' && (
              <Link onClick={() => setOpen(false)} to="/account/settings" className="mt-1 flex items-center gap-3 rounded-2xl px-3 py-2.5 text-sm font-semibold text-ink-700 hover:bg-ink-50">
                <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-ink-100 text-ink-500">
                  <UserIcon className="h-4 w-4" />
                </span>
                Sozlamalar
              </Link>
            )}
            <div className="my-1 h-px bg-ink-100" />
            <button
              onClick={async () => {
                await logout()
                nav('/')
              }}
              className="flex w-full items-center gap-3 rounded-2xl px-3 py-2.5 text-sm font-semibold text-rose-600 hover:bg-rose-50"
            >
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-rose-50">
                <LogOut className="h-4 w-4" />
              </span>
              Chiqish
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
