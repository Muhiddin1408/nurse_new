import { Link } from 'react-router-dom'
import { cx } from './ui'

export function LogoMark({ size = 36 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden>
      <defs>
        <linearGradient id="tc-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#34d3ad" />
          <stop offset="1" stopColor="#047863" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="18" fill="url(#tc-g)" />
      <path d="M27 16h10v11h11v10H37v11H27V37H16V27h11z" fill="#fff" />
    </svg>
  )
}

export function Logo({ to = '/', light, className }: { to?: string; light?: boolean; className?: string }) {
  return (
    <Link to={to} className={cx('flex items-center gap-2.5', className)}>
      <LogoMark />
      <span className="leading-none">
        <span className={cx('block text-[17px] font-extrabold tracking-tight', light ? 'text-white' : 'text-ink-900')}>
          Turon <span className={light ? 'text-brand-300' : 'text-brand-600'}>Clinic</span>
        </span>
        <span className={cx('block text-[10.5px] font-semibold uppercase tracking-[0.14em]', light ? 'text-white/50' : 'text-ink-400')}>sog'lig'ingiz — bizda</span>
      </span>
    </Link>
  )
}
