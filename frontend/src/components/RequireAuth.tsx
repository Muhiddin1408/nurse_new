import type { ReactNode } from 'react'
import { Link, Navigate, useLocation } from 'react-router-dom'
import { ShieldAlert } from 'lucide-react'
import { ROLE_LABEL, useAuth } from '@/lib/auth'
import { useToast } from '@/lib/toast'
import type { Role } from '@/lib/types'
import { Button } from './ui'

/** Token yo'q → login; rol mos emas → o'tish taklifi (oq ekran qolmaydi). */
export function RequireAuth({ role, children }: { role?: Role; children: ReactNode }) {
  const { isAuthed, user, switchRole } = useAuth()
  const toast = useToast()
  const loc = useLocation()

  if (!isAuthed || !user) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />

  if (role && user.active_role !== role) {
    const has = user.available_roles?.includes(role)
    return (
      <div className="flex min-h-[70vh] items-center justify-center px-4">
        <div className="card max-w-md p-8 text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-amber-50 text-amber-600">
            <ShieldAlert className="h-7 w-7" />
          </div>
          <h2 className="text-xl font-bold">{ROLE_LABEL[role]} kabineti</h2>
          <p className="mt-2 text-ink-500">
            {has
              ? `Hozir siz "${ROLE_LABEL[user.active_role]}" rejimidasiz. Davom etish uchun rejimni almashtiring.`
              : `Bu bo'lim faqat "${ROLE_LABEL[role]}" uchun. Akkauntingizda bu rol yo'q.`}
          </p>
          <div className="mt-6 flex justify-center gap-2">
            <Link to="/">
              <Button variant="outline">Bosh sahifa</Button>
            </Link>
            {has && (
              <Button
                onClick={() =>
                  switchRole(role).catch((e) => toast.error(e))
                }
              >
                {ROLE_LABEL[role]} rejimiga o'tish
              </Button>
            )}
          </div>
        </div>
      </div>
    )
  }
  return <>{children}</>
}
