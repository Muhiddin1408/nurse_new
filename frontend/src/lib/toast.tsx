import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { CheckCircle2, AlertTriangle, Info, X } from 'lucide-react'
import { errText } from './api'

type Kind = 'success' | 'error' | 'info'
interface Toast { id: number; kind: Kind; text: string }

interface ToastCtx {
  success: (text: string) => void
  error: (e: unknown) => void
  info: (text: string) => void
}

const Ctx = createContext<ToastCtx | null>(null)
let seq = 0

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([])

  const push = useCallback((kind: Kind, text: string) => {
    const id = ++seq
    setItems((s) => [...s.slice(-3), { id, kind, text }])
    setTimeout(() => setItems((s) => s.filter((t) => t.id !== id)), kind === 'error' ? 6000 : 3800)
  }, [])

  const value: ToastCtx = {
    success: (t) => push('success', t),
    error: (e) => push('error', typeof e === 'string' ? e : errText(e)),
    info: (t) => push('info', t),
  }

  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-4 z-[100] flex flex-col items-center gap-2 px-4 sm:bottom-6 sm:items-end sm:pr-6">
        <AnimatePresence>
          {items.map((t) => (
            <motion.div
              key={t.id}
              layout
              initial={{ opacity: 0, y: 20, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 10, scale: 0.96 }}
              className="pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-2xl bg-ink-900/95 px-4 py-3 text-sm text-white shadow-lift backdrop-blur"
            >
              {t.kind === 'success' && <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-brand-400" />}
              {t.kind === 'error' && <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-rose-400" />}
              {t.kind === 'info' && <Info className="mt-0.5 h-5 w-5 shrink-0 text-sky-400" />}
              <span className="flex-1 leading-snug">{t.text}</span>
              <button onClick={() => setItems((s) => s.filter((x) => x.id !== t.id))} className="text-white/50 hover:text-white">
                <X className="h-4 w-4" />
              </button>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </Ctx.Provider>
  )
}

export function useToast() {
  const c = useContext(Ctx)
  if (!c) throw new Error('useToast must be used inside ToastProvider')
  return c
}
