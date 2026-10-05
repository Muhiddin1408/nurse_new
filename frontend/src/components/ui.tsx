import { forwardRef, useEffect, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { clsx } from 'clsx'
import { AlertTriangle, Inbox, Loader2, RefreshCw, Star, X } from 'lucide-react'
import { errText } from '@/lib/api'
import { initials, money } from '@/lib/format'

export const cx = clsx

// ---- Button ----------------------------------------------------------------
type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline' | 'dark'
type Size = 'sm' | 'md' | 'lg'

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-gradient-to-b from-brand-500 to-brand-600 text-white shadow-glow hover:from-brand-500 hover:to-brand-700 active:scale-[.98]',
  secondary: 'bg-brand-50 text-brand-700 hover:bg-brand-100',
  ghost: 'text-ink-600 hover:bg-ink-100 hover:text-ink-900',
  danger: 'bg-rose-600 text-white hover:bg-rose-700 shadow-[0_10px_30px_-10px_rgba(225,29,72,.55)]',
  outline: 'border border-ink-200 bg-white text-ink-800 hover:border-ink-300 hover:bg-ink-50',
  dark: 'bg-ink-900 text-white hover:bg-ink-800',
}
const SIZES: Record<Size, string> = {
  sm: 'h-9 px-3.5 text-sm rounded-xl gap-1.5',
  md: 'h-11 px-5 text-[15px] rounded-2xl gap-2',
  lg: 'h-14 px-7 text-base rounded-2xl gap-2.5',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
  block?: boolean
  icon?: ReactNode
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', loading, block, icon, className, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cx(
        'inline-flex select-none items-center justify-center font-semibold transition-all duration-150 disabled:pointer-events-none disabled:opacity-50',
        VARIANTS[variant],
        SIZES[size],
        block && 'w-full',
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : icon}
      {children}
    </button>
  )
})

// ---- Form fields -----------------------------------------------------------
interface FieldWrapProps {
  label?: ReactNode
  error?: string
  hint?: ReactNode
  children: ReactNode
  className?: string
}
export function Field({ label, error, hint, children, className }: FieldWrapProps) {
  return (
    <label className={cx('block', className)}>
      {label && <span className="label">{label}</span>}
      {children}
      {error ? <span className="mt-1.5 block text-sm font-medium text-rose-600">{error}</span> : hint ? <span className="mt-1.5 block text-xs text-ink-500">{hint}</span> : null}
    </label>
  )
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }>(function Input(
  { className, invalid, ...rest },
  ref,
) {
  return <input ref={ref} className={cx('field', invalid && 'border-rose-400 focus:border-rose-500 focus:ring-rose-500/15', className)} {...rest} />
})

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...rest }, ref) {
  return <textarea ref={ref} rows={3} className={cx('field resize-none', className)} {...rest} />
})

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select({ className, children, ...rest }, ref) {
  return (
    <select
      ref={ref}
      className={cx(
        'field appearance-none bg-[url("data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 width=%2720%27 height=%2720%27 fill=%27none%27 stroke=%27%23647596%27 stroke-width=%272%27%3E%3Cpath d=%27m6 8 4 4 4-4%27/%3E%3C/svg%3E")] bg-[right_0.9rem_center] bg-no-repeat pr-10',
        className,
      )}
      {...rest}
    >
      {children}
    </select>
  )
})

export function Toggle({ checked, onChange, disabled }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx('relative h-7 w-12 shrink-0 rounded-full transition-colors disabled:opacity-50', checked ? 'bg-brand-500' : 'bg-ink-200')}
    >
      <span className={cx('absolute top-1 h-5 w-5 rounded-full bg-white shadow transition-all', checked ? 'left-6' : 'left-1')} />
    </button>
  )
}

// ---- Segmented control -----------------------------------------------------
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  className,
  size = 'md',
}: {
  value: T
  onChange: (v: T) => void
  options: { value: T; label: ReactNode; icon?: ReactNode; count?: number }[]
  className?: string
  size?: 'sm' | 'md'
}) {
  return (
    <div className={cx('inline-flex rounded-2xl bg-ink-100/80 p-1', className)}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={cx(
            'relative flex flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded-xl font-semibold transition',
            size === 'sm' ? 'px-3 py-1.5 text-sm' : 'px-4 py-2 text-sm sm:text-[15px]',
            value === o.value ? 'text-ink-900' : 'text-ink-500 hover:text-ink-800',
          )}
        >
          {value === o.value && <motion.span layoutId={`seg-${options.map((x) => x.value).join('')}`} className="absolute inset-0 rounded-xl bg-white shadow-soft" transition={{ type: 'spring', bounce: 0.2, duration: 0.4 }} />}
          <span className="relative flex items-center gap-1.5">
            {o.icon}
            {o.label}
            {o.count !== undefined && o.count > 0 && <span className="rounded-full bg-brand-500 px-1.5 text-[11px] text-white">{o.count}</span>}
          </span>
        </button>
      ))}
    </div>
  )
}

// ---- Avatar ----------------------------------------------------------------
const AVATAR_GRADIENTS = [
  'from-emerald-400 to-teal-600',
  'from-sky-400 to-indigo-600',
  'from-violet-400 to-fuchsia-600',
  'from-amber-400 to-orange-600',
  'from-rose-400 to-pink-600',
  'from-cyan-400 to-blue-600',
]
export function Avatar({ name, size = 48, className }: { name?: string | null; size?: number; className?: string }) {
  const n = name || '?'
  let h = 0
  for (let i = 0; i < n.length; i++) h = (h * 31 + n.charCodeAt(i)) >>> 0
  return (
    <div
      className={cx('flex shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br font-bold text-white shadow-inner', AVATAR_GRADIENTS[h % AVATAR_GRADIENTS.length], className)}
      style={{ width: size, height: size, fontSize: size * 0.36 }}
    >
      {initials(n)}
    </div>
  )
}

// ---- Rating ----------------------------------------------------------------
export function Stars({ value, size = 16, onChange }: { value: number; size?: number; onChange?: (v: number) => void }) {
  return (
    <div className="flex items-center gap-0.5">
      {[1, 2, 3, 4, 5].map((i) => (
        <button key={i} type="button" disabled={!onChange} onClick={() => onChange?.(i)} className={cx(onChange && 'transition hover:scale-110')}>
          <Star style={{ width: size, height: size }} className={i <= Math.round(value) ? 'fill-amber-400 text-amber-400' : 'fill-ink-100 text-ink-200'} />
        </button>
      ))}
    </div>
  )
}

export function RatingPill({ rating, count, isNew }: { rating: number | null | undefined; count?: number; isNew?: boolean }) {
  if (isNew || !rating) return <span className="chip bg-sky-50 text-sky-700">✨ Yangi shifokor</span>
  return (
    <span className="chip bg-amber-50 text-amber-700">
      <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-400" />
      {Number(rating).toFixed(1)}
      {count !== undefined && <span className="font-medium text-amber-600/70">({count})</span>}
    </span>
  )
}

// ---- Money -----------------------------------------------------------------
export function Money({ value, className, strike }: { value: string | number | null | undefined; className?: string; strike?: boolean }) {
  return <span className={cx('tabular-nums', strike && 'text-ink-400 line-through', className)}>{money(value)}</span>
}

// ---- Badges ----------------------------------------------------------------
type Tone = 'green' | 'blue' | 'amber' | 'gray' | 'red' | 'violet'
const TONES: Record<Tone, string> = {
  green: 'bg-emerald-50 text-emerald-700 ring-emerald-600/15',
  blue: 'bg-sky-50 text-sky-700 ring-sky-600/15',
  amber: 'bg-amber-50 text-amber-700 ring-amber-600/20',
  gray: 'bg-ink-100 text-ink-600 ring-ink-500/10',
  red: 'bg-rose-50 text-rose-700 ring-rose-600/15',
  violet: 'bg-violet-50 text-violet-700 ring-violet-600/15',
}
export function Badge({ tone = 'gray', children, dot, className }: { tone?: Tone; children: ReactNode; dot?: boolean; className?: string }) {
  return (
    <span className={cx('inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset', TONES[tone], className)}>
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  )
}

export const BOOKING_STATUS: Record<string, { label: string; tone: Tone }> = {
  pending_payment: { label: "To'lov kutilmoqda", tone: 'amber' },
  confirmed: { label: 'Tasdiqlangan', tone: 'blue' },
  in_progress: { label: 'Qabulda', tone: 'violet' },
  completed: { label: 'Yakunlangan', tone: 'green' },
  cancelled: { label: 'Bekor qilingan', tone: 'gray' },
  expired: { label: 'Muddati o‘tgan', tone: 'gray' },
  no_show: { label: 'Kelmadi', tone: 'red' },
}

export function StatusBadge({ status, map = BOOKING_STATUS, fallback }: { status: string; map?: Record<string, { label: string; tone: Tone }>; fallback?: string }) {
  const s = map[status] ?? { label: fallback || status, tone: 'gray' as Tone }
  return (
    <Badge tone={s.tone} dot>
      {s.label}
    </Badge>
  )
}

// ---- States ----------------------------------------------------------------
export function Skeleton({ className }: { className?: string }) {
  return (
    <div className={cx('relative overflow-hidden rounded-xl bg-ink-100', className)}>
      <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white/60 to-transparent" />
    </div>
  )
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cx('h-6 w-6 animate-spin text-brand-500', className)} />
}

export function EmptyState({ icon, title, text, action, className }: { icon?: ReactNode; title: string; text?: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <div className={cx('flex flex-col items-center px-6 py-14 text-center', className)}>
      <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-3xl bg-gradient-to-br from-brand-50 to-sky-50 text-brand-600 ring-1 ring-brand-100">
        {icon ?? <Inbox className="h-7 w-7" />}
      </div>
      <h3 className="text-lg font-bold text-ink-900">{title}</h3>
      {text && <p className="mt-1.5 max-w-sm text-[15px] text-ink-500">{text}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}

export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  return (
    <div className={cx('flex flex-col items-center px-6 py-12 text-center', className)}>
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-rose-50 text-rose-600">
        <AlertTriangle className="h-6 w-6" />
      </div>
      <h3 className="font-bold text-ink-900">Ma'lumotni yuklab bo'lmadi</h3>
      <p className="mt-1 max-w-sm text-sm text-ink-500">{errText(error)}</p>
      {onRetry && (
        <Button variant="outline" size="sm" className="mt-4" onClick={onRetry} icon={<RefreshCw className="h-4 w-4" />}>
          Qayta urinish
        </Button>
      )}
    </div>
  )
}

// ---- Modal -----------------------------------------------------------------
export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  size = 'md',
}: {
  open: boolean
  onClose: () => void
  title?: ReactNode
  children: ReactNode
  footer?: ReactNode
  size?: 'sm' | 'md' | 'lg'
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [open, onClose])

  return (
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-[90] flex items-end justify-center sm:items-center sm:p-4">
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="absolute inset-0 bg-ink-950/50 backdrop-blur-sm" onClick={onClose} />
          <motion.div
            initial={{ opacity: 0, y: 40, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 30, scale: 0.98 }}
            transition={{ type: 'spring', bounce: 0.15, duration: 0.45 }}
            className={cx(
              'relative flex max-h-[92vh] w-full flex-col overflow-hidden rounded-t-4xl bg-white shadow-lift sm:rounded-4xl',
              size === 'sm' && 'sm:max-w-md',
              size === 'md' && 'sm:max-w-lg',
              size === 'lg' && 'sm:max-w-2xl',
            )}
          >
            <div className="flex items-center justify-between gap-4 px-6 pb-2 pt-5">
              <h3 className="text-lg font-bold text-ink-900">{title}</h3>
              <button onClick={onClose} className="rounded-xl p-2 text-ink-400 transition hover:bg-ink-100 hover:text-ink-700">
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="overflow-y-auto px-6 pb-6 pt-2">{children}</div>
            {footer && <div className="flex flex-col-reverse gap-2 border-t border-ink-100 bg-ink-50/60 px-6 py-4 sm:flex-row sm:justify-end">{footer}</div>}
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  )
}

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  text,
  confirmText = 'Tasdiqlash',
  danger,
  loading,
  children,
}: {
  open: boolean
  onClose: () => void
  onConfirm: () => void
  title: string
  text?: ReactNode
  confirmText?: string
  danger?: boolean
  loading?: boolean
  children?: ReactNode
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Bekor
          </Button>
          <Button variant={danger ? 'danger' : 'primary'} loading={loading} onClick={onConfirm}>
            {confirmText}
          </Button>
        </>
      }
    >
      {text && <p className="text-[15px] leading-relaxed text-ink-600">{text}</p>}
      {children}
    </Modal>
  )
}

// ---- Layout helpers --------------------------------------------------------
export function PageHeader({ title, subtitle, actions, back }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; back?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div>
        {back}
        <h1 className="text-2xl font-extrabold text-ink-900 sm:text-3xl">{title}</h1>
        {subtitle && <p className="mt-1 text-[15px] text-ink-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  )
}

export function Stat({ label, value, icon, tone = 'brand', hint }: { label: string; value: ReactNode; icon?: ReactNode; tone?: 'brand' | 'sky' | 'amber' | 'violet' | 'rose'; hint?: ReactNode }) {
  const tones = {
    brand: 'from-brand-50 to-brand-100 text-brand-700',
    sky: 'from-sky-50 to-sky-100 text-sky-700',
    amber: 'from-amber-50 to-amber-100 text-amber-700',
    violet: 'from-violet-50 to-violet-100 text-violet-700',
    rose: 'from-rose-50 to-rose-100 text-rose-700',
  }
  return (
    <div className="card p-5">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-ink-500">{label}</span>
        {icon && <span className={cx('flex h-10 w-10 items-center justify-center rounded-2xl bg-gradient-to-br', tones[tone])}>{icon}</span>}
      </div>
      <div className="mt-2 text-2xl font-extrabold tabular-nums text-ink-900">{value}</div>
      {hint && <div className="mt-1 text-xs text-ink-500">{hint}</div>}
    </div>
  )
}

export function ListSkeleton({ rows = 4, className }: { rows?: number; className?: string }) {
  return (
    <div className={cx('space-y-3', className)}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="card flex items-center gap-4 p-4">
          <Skeleton className="h-14 w-14 rounded-2xl" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-3 w-1/3" />
          </div>
        </div>
      ))}
    </div>
  )
}
