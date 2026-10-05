import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import {
  ArrowLeft, Building2, CalendarClock, CalendarPlus, CalendarSync, CircleCheckBig, CreditCard, ExternalLink, Flag, House, Loader2, MapPin, Navigation,
  RotateCcw, Star, TimerReset, UserRound, Wallet, XCircle,
} from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { dateLong, money, relativeDateTime, time, todayISO, weekday, dayKey } from '@/lib/format'
import { useToast } from '@/lib/toast'
import type { Booking, CancellationPreview, CheckoutResponse } from '@/lib/types'
import { SlotPicker, type PlacedSlot } from '@/components/SlotPicker'
import { Avatar, Badge, Button, ErrorState, Modal, Money, Select, Skeleton, Stars, StatusBadge, Textarea, Toggle, cx } from '@/components/ui'

const PAYMENT_MODE: Record<string, string> = {
  prepaid: "To'liq oldindan to'lov",
  deposit: "Qisman oldindan to'lov (depozit)",
  at_clinic: "Klinikada to'lanadi",
}

const DISCOUNT_LABEL: Record<string, string> = {
  promo: 'Promo kod',
  first_booking: 'Birinchi bron chegirmasi',
  follow_up: 'Takroriy qabul chegirmasi',
  package: 'Paket chegirmasi',
}

export default function BookingDetail() {
  const { id = '' } = useParams()
  const [sp] = useSearchParams()
  const isNew = sp.get('new') === '1'
  const [polling, setPolling] = useState(false)

  const q = useQuery({
    queryKey: ['booking', id],
    queryFn: () => api<Booking>(`/booking/bookings/${id}`),
    // To'lov natijasi webhook orqali keladi — haqiqat manbai shu endpoint (3s polling)
    refetchInterval: (query) => (polling && query.state.data?.status === 'pending_payment' ? 3000 : false),
  })

  const b = q.data
  useEffect(() => {
    if (b && b.status !== 'pending_payment') setPolling(false)
  }, [b])

  if (q.isError)
    return (
      <div className="card">
        <ErrorState error={(q.error as ApiError).status === 404 ? new Error('Bron topilmadi') : q.error} onRetry={() => q.refetch()} />
      </div>
    )
  if (!b)
    return (
      <div className="space-y-4">
        <Skeleton className="h-40 rounded-3xl" />
        <Skeleton className="h-64 rounded-3xl" />
      </div>
    )

  return (
    <div>
      <Link to="/account/bookings" className="mb-4 inline-flex items-center gap-1.5 text-sm font-semibold text-ink-500 hover:text-ink-900">
        <ArrowLeft className="h-4 w-4" /> Bronlarim
      </Link>

      {isNew && b.status === 'confirmed' && <SuccessHero b={b} />}

      <div className="grid gap-6 xl:grid-cols-[1fr_340px]">
        <div className="min-w-0 space-y-6">
          <div className="card overflow-hidden">
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-ink-100 p-5 sm:p-6">
              <div className="flex items-center gap-4">
                <Avatar name={(b.doctor_name ?? '').replace(/^Dr\.?\s*/, '')} size={56} />
                <div>
                  <Link to={`/doctors/${b.doctor_id}`} className="text-lg font-bold text-ink-900 hover:text-brand-700">
                    {b.doctor_name}
                  </Link>
                  <div className="text-sm text-ink-500">Bron № {b.number}</div>
                </div>
              </div>
              <StatusBadge status={b.status} />
            </div>
            <div className="grid gap-px bg-ink-100 sm:grid-cols-2">
              <Info icon={<CalendarClock className="h-5 w-5" />} label="Sana va vaqt" value={b.start_at ? `${weekday(dayKey(b.start_at))}, ${dateLong(b.start_at)}` : '—'} sub={b.start_at ? `${time(b.start_at)} – ${time(b.end_at)}` : undefined} />
              <Info
                icon={b.place === 'home' ? <House className="h-5 w-5" /> : <Building2 className="h-5 w-5" />}
                label={b.place === 'home' ? 'Uyga chaqiruv' : 'Klinika'}
                value={b.place === 'home' ? b.address_text || 'Manzil' : b.clinic_name || '—'}
                sub={b.place === 'home' ? undefined : b.clinic_address}
              />
              <Info icon={<UserRound className="h-5 w-5" />} label="Bemor" value={b.patient_name ?? '—'} />
              <Info icon={<Wallet className="h-5 w-5" />} label="To'lov usuli" value={PAYMENT_MODE[b.payment_mode ?? ''] ?? '—'} sub={b.payment_mode === 'deposit' && b.prepay_amount ? `Oldindan: ${money(b.prepay_amount)}` : undefined} />
            </div>
          </div>

          {b.status === 'pending_payment' && <PaymentPanel b={b} onStartPolling={() => setPolling(true)} polling={polling} />}

          <div className="card p-5 sm:p-6">
            <h3 className="mb-4 font-bold text-ink-900">Xizmatlar</h3>
            <div className="space-y-3">
              {b.items.map((it, i) => (
                <div key={i} className="flex justify-between gap-4 text-[15px]">
                  <span className="text-ink-700">
                    {it.service_name} <span className="text-sm text-ink-400">· {it.duration_minutes} daq</span>
                  </span>
                  <Money value={it.price} className="font-semibold text-ink-900" />
                </div>
              ))}
              {Number(b.discount_amount) > 0 && (
                <div className="flex justify-between text-[15px] font-semibold text-brand-700">
                  <span>
                    {DISCOUNT_LABEL[b.discount_kind] ?? 'Chegirma'}
                    {b.promo_code && ` (${b.promo_code})`}
                  </span>
                  <span>−{money(b.discount_amount)}</span>
                </div>
              )}
              <div className="flex items-end justify-between border-t border-ink-100 pt-3">
                <span className="font-semibold text-ink-700">Jami</span>
                <span>
                  {b.original_price && b.original_price !== b.total_price && <Money value={b.original_price} strike className="mr-2 text-sm" />}
                  <Money value={b.total_price} className="text-2xl font-extrabold text-ink-950" />
                </span>
              </div>
              {b.status === 'confirmed' && b.payment_mode !== 'prepaid' && (
                <div className="rounded-2xl bg-amber-50 p-3 text-sm text-amber-800">
                  Qabulda to'lanadi: <b>{money(subtract(b.total_price, b.payment_mode === 'deposit' ? b.prepay_amount ?? '0' : '0'))}</b>
                </div>
              )}
            </div>
          </div>
        </div>

        <Actions b={b} />
      </div>
    </div>
  )
}

function subtract(a: string, c: string) {
  const toT = (v: string) => {
    const [i, f = ''] = v.split('.')
    return BigInt(i) * 100n + BigInt((f + '00').slice(0, 2))
  }
  const r = toT(a) - toT(c)
  return `${r / 100n}.${String(r % 100n).padStart(2, '0')}`
}

function Info({ icon, label, value, sub }: { icon: React.ReactNode; label: string; value: string; sub?: string }) {
  return (
    <div className="flex gap-3 bg-white p-5">
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-50 text-brand-600">{icon}</span>
      <div className="min-w-0">
        <div className="text-xs font-semibold uppercase tracking-wide text-ink-400">{label}</div>
        <div className="font-semibold text-ink-900">{value}</div>
        {sub && <div className="text-sm text-ink-500">{sub}</div>}
      </div>
    </div>
  )
}

function SuccessHero({ b }: { b: Booking }) {
  return (
    <motion.div initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} className="relative mb-6 overflow-hidden rounded-4xl bg-gradient-to-br from-brand-500 to-teal-700 p-6 text-white sm:p-8">
      <div className="absolute -right-10 -top-10 h-48 w-48 rounded-full bg-white/10" />
      <div className="relative flex flex-col gap-4 sm:flex-row sm:items-center">
        <motion.span initial={{ scale: 0, rotate: -45 }} animate={{ scale: 1, rotate: 0 }} transition={{ type: 'spring', delay: 0.15 }} className="flex h-16 w-16 items-center justify-center rounded-3xl bg-white text-brand-600">
          <CircleCheckBig className="h-9 w-9" />
        </motion.span>
        <div className="flex-1">
          <h2 className="text-2xl font-extrabold">Bron tasdiqlandi!</h2>
          <p className="mt-1 text-white/85">
            {relativeDateTime(b.start_at)} — {b.doctor_name}. Eslatmani SMS orqali yuboramiz.
          </p>
        </div>
        <Button variant="dark" onClick={() => downloadIcs(b)} icon={<CalendarPlus className="h-4 w-4" />}>
          Kalendarga qo'shish
        </Button>
      </div>
    </motion.div>
  )
}

function downloadIcs(b: Booking) {
  if (!b.start_at || !b.end_at) return
  const f = (iso: string) => new Date(iso).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '')
  const loc = b.place === 'home' ? b.address_text : `${b.clinic_name}, ${b.clinic_address}`
  const ics = [
    'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Turon Clinic//UZ', 'BEGIN:VEVENT',
    `UID:${b.id}@turonclinic.uz`, `DTSTAMP:${f(new Date().toISOString())}`, `DTSTART:${f(b.start_at)}`, `DTEND:${f(b.end_at)}`,
    `SUMMARY:Qabul: ${b.doctor_name}`, `LOCATION:${loc ?? ''}`, `DESCRIPTION:Bron № ${b.number}`,
    'BEGIN:VALARM', 'TRIGGER:-PT2H', 'ACTION:DISPLAY', 'DESCRIPTION:Qabulga 2 soat qoldi', 'END:VALARM', 'END:VEVENT', 'END:VCALENDAR',
  ].join('\r\n')
  const url = URL.createObjectURL(new Blob([ics], { type: 'text/calendar' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `turon-clinic-${b.number}.ics`
  a.click()
  URL.revokeObjectURL(url)
}

function Countdown({ until }: { until: string }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  const left = Math.max(0, Math.floor((new Date(until).getTime() - now) / 1000))
  return (
    <span className={cx('font-mono text-lg font-bold tabular-nums', left < 120 ? 'text-rose-600' : 'text-amber-700')}>
      {Math.floor(left / 60)}:{String(left % 60).padStart(2, '0')}
    </span>
  )
}

function PaymentPanel({ b, onStartPolling, polling }: { b: Booking; onStartPolling: () => void; polling: boolean }) {
  const toast = useToast()
  const [provider, setProvider] = useState<'payme' | 'click'>('payme')
  const [checkout, setCheckout] = useState<CheckoutResponse | null>(null)

  const m = useMutation({
    mutationFn: () => api<CheckoutResponse>(`/payment/bookings/${b.id}/checkout`, { method: 'POST', body: { provider } }),
    onSuccess: (c) => {
      setCheckout(c)
      onStartPolling()
      window.open(c.checkout_url, '_blank', 'noopener')
    },
    onError: (e) => toast.error(e),
  })

  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="card overflow-hidden ring-2 ring-amber-200">
      <div className="flex flex-wrap items-center justify-between gap-3 bg-amber-50 px-5 py-4 sm:px-6">
        <div className="flex items-center gap-2 font-bold text-amber-900">
          <TimerReset className="h-5 w-5" /> To'lovni yakunlang
        </div>
        {checkout?.expires_at && (
          <div className="flex items-center gap-2 text-sm text-amber-800">
            Vaqt saqlanadi: <Countdown until={checkout.expires_at} />
          </div>
        )}
      </div>
      <div className="p-5 sm:p-6">
        <p className="text-[15px] text-ink-600">
          Vaqtingiz vaqtincha band qilindi. To'lov qilinmasa, u avtomatik bo'shatiladi. To'lanadigan summa: <b className="text-ink-900">{money(b.prepay_amount ?? b.total_price)}</b>
        </p>
        <div className="mt-5 grid grid-cols-2 gap-3">
          {(['payme', 'click'] as const).map((p) => (
            <button
              key={p}
              onClick={() => setProvider(p)}
              className={cx('flex h-16 items-center justify-center rounded-2xl border-2 text-xl font-extrabold transition', provider === p ? 'border-brand-500 bg-brand-50/50' : 'border-ink-100 hover:border-ink-200')}
            >
              {p === 'payme' ? <span className="text-[#33cccc]">pay<span className="text-ink-800">me</span></span> : <span className="text-[#0099ff]">click</span>}
            </button>
          ))}
        </div>
        <Button block size="lg" className="mt-5" loading={m.isPending} onClick={() => m.mutate()} icon={<CreditCard className="h-5 w-5" />}>
          {checkout ? "To'lov sahifasini qayta ochish" : `${provider === 'payme' ? 'Payme' : 'Click'} orqali to'lash`}
        </Button>
        {checkout && (
          <a href={checkout.checkout_url} target="_blank" rel="noreferrer" className="mt-3 flex items-center justify-center gap-1.5 text-sm font-semibold text-brand-600">
            Havola ochilmadimi? Bu yerni bosing <ExternalLink className="h-4 w-4" />
          </a>
        )}
        {polling && (
          <div className="mt-4 flex items-center justify-center gap-2 rounded-2xl bg-ink-50 py-3 text-sm text-ink-600">
            <Loader2 className="h-4 w-4 animate-spin text-brand-500" /> To'lov tasdig'ini kutmoqdamiz...
          </div>
        )}
      </div>
    </motion.div>
  )
}

function Actions({ b }: { b: Booking }) {
  const nav = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()
  const [modal, setModal] = useState<null | 'cancel' | 'reschedule' | 'review' | 'dispute'>(null)

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['booking', b.id] })
    qc.invalidateQueries({ queryKey: ['bookings'] })
  }

  const rebook = useMutation({
    mutationFn: () => api<{ doctor_id: string; doctor_bookable: boolean; place: string }>(`/booking/bookings/${b.id}/rebook`),
    onSuccess: (r) => {
      if (!r.doctor_bookable) return toast.error("Shifokor hozir bron qabul qilmayapti")
      nav(`/book/${r.doctor_id}?place=${r.place}`)
    },
    onError: (e) => toast.error(e),
  })

  const s = b.status
  const mapsQuery = b.place === 'home' ? '' : encodeURIComponent(`${b.clinic_name ?? ''} ${b.clinic_address ?? ''}`)

  return (
    <aside className="space-y-3 xl:sticky xl:top-24 xl:self-start">
      <div className="card space-y-2 p-4">
        <div className="px-1 pb-1 text-xs font-bold uppercase tracking-wider text-ink-400">Amallar</div>
        {s === 'confirmed' && b.start_at && (
          <Button variant="outline" block onClick={() => downloadIcs(b)} icon={<CalendarPlus className="h-4 w-4" />}>
            Kalendarga qo'shish
          </Button>
        )}
        {s === 'confirmed' && mapsQuery && (
          <a href={`https://www.google.com/maps/search/?api=1&query=${mapsQuery}`} target="_blank" rel="noreferrer" className="block">
            <Button variant="outline" block icon={<Navigation className="h-4 w-4" />}>
              Yo'nalish
            </Button>
          </a>
        )}
        {s === 'confirmed' && (
          <Button variant="outline" block onClick={() => setModal('reschedule')} icon={<CalendarSync className="h-4 w-4" />}>
            Vaqtni o'zgartirish
          </Button>
        )}
        {s === 'completed' && (
          <Button block onClick={() => setModal('review')} icon={<Star className="h-4 w-4" />}>
            Sharh yozish
          </Button>
        )}
        {['completed', 'cancelled', 'expired'].includes(s) && (
          <Button variant={s === 'completed' ? 'outline' : 'primary'} block loading={rebook.isPending} onClick={() => rebook.mutate()} icon={<RotateCcw className="h-4 w-4" />}>
            Qayta bron qilish
          </Button>
        )}
        {['completed', 'no_show'].includes(s) && (
          <Button variant="ghost" block onClick={() => setModal('dispute')} icon={<Flag className="h-4 w-4" />}>
            Muammo haqida xabar berish
          </Button>
        )}
        {['pending_payment', 'confirmed'].includes(s) && (
          <Button variant="ghost" block className="text-rose-600 hover:bg-rose-50 hover:text-rose-700" onClick={() => setModal('cancel')} icon={<XCircle className="h-4 w-4" />}>
            Bronni bekor qilish
          </Button>
        )}
        {s === 'expired' && <p className="px-1 text-sm text-ink-500">To'lov vaqti tugagan — vaqt bo'shatildi. Qaytadan bron qiling.</p>}
      </div>
      <div className="card p-4 text-sm text-ink-500">
        <div className="flex items-center gap-2 font-semibold text-ink-800">
          <MapPin className="h-4 w-4 text-brand-600" /> Yordam kerakmi?
        </div>
        <p className="mt-1">Qo'llab-quvvatlash: +998 71 200-01-01 yoki Telegram @turonclinic_bot</p>
      </div>

      <CancelModal open={modal === 'cancel'} onClose={() => setModal(null)} b={b} onDone={refresh} />
      <RescheduleModal open={modal === 'reschedule'} onClose={() => setModal(null)} b={b} onDone={refresh} />
      <ReviewModal open={modal === 'review'} onClose={() => setModal(null)} b={b} />
      <DisputeModal open={modal === 'dispute'} onClose={() => setModal(null)} b={b} />
    </aside>
  )
}

function CancelModal({ open, onClose, b, onDone }: { open: boolean; onClose: () => void; b: Booking; onDone: () => void }) {
  const toast = useToast()
  const [reason, setReason] = useState('')
  // Jarimani ko'rsatmasdan bekor qilish — support'ga shikoyat manbai: avval preview
  const preview = useQuery({
    queryKey: ['cancel-preview', b.id],
    queryFn: () => api<CancellationPreview>(`/booking/bookings/${b.id}/cancellation-preview`),
    enabled: open,
    staleTime: 0,
  })
  const m = useMutation({
    mutationFn: () => api<Booking>(`/booking/bookings/${b.id}/cancel`, { method: 'POST', body: { reason } }),
    onSuccess: () => {
      toast.success('Bron bekor qilindi')
      onDone()
      onClose()
    },
    onError: (e) => toast.error(e),
  })
  const p = preview.data
  const paid = b.status === 'confirmed' && b.payment_mode !== 'at_clinic'
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Bronni bekor qilasizmi?"
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Yo'q, qoldirish
          </Button>
          <Button variant="danger" loading={m.isPending} disabled={!p?.allowed} onClick={() => m.mutate()}>
            Ha, bekor qilish
          </Button>
        </>
      }
    >
      {preview.isLoading ? (
        <Skeleton className="h-24 rounded-2xl" />
      ) : preview.isError ? (
        <ErrorState error={preview.error} onRetry={() => preview.refetch()} />
      ) : p && !p.allowed ? (
        <div className="rounded-2xl bg-rose-50 p-4 text-sm text-rose-700">Bu bronni endi bekor qilib bo'lmaydi (qabul boshlangan yoki yakunlangan).</div>
      ) : (
        p && (
          <div className="space-y-4">
            {paid ? (
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-2xl bg-brand-50 p-4">
                  <div className="text-xs font-semibold uppercase text-brand-700">Qaytariladi</div>
                  <div className="mt-1 text-xl font-extrabold text-brand-800">{money(p.refund_amount)}</div>
                </div>
                <div className={cx('rounded-2xl p-4', Number(p.penalty_amount) > 0 ? 'bg-rose-50' : 'bg-ink-50')}>
                  <div className={cx('text-xs font-semibold uppercase', Number(p.penalty_amount) > 0 ? 'text-rose-700' : 'text-ink-500')}>Jarima</div>
                  <div className={cx('mt-1 text-xl font-extrabold', Number(p.penalty_amount) > 0 ? 'text-rose-700' : 'text-ink-700')}>{money(p.penalty_amount)}</div>
                </div>
              </div>
            ) : (
              <p className="text-[15px] text-ink-600">Siz hali to'lov qilmagansiz — bekor qilish bepul.</p>
            )}
            {paid && Number(p.penalty_amount) > 0 && <p className="text-sm text-ink-500">Qabulga 24 soatdan kam vaqt qolgani uchun summaning bir qismi shifokorga kompensatsiya sifatida o'tkaziladi.</p>}
            <div>
              <span className="label">Sabab (ixtiyoriy)</span>
              <Select value={reason} onChange={(e) => setReason(e.target.value)}>
                <option value="">Tanlang</option>
                <option>Rejalarim o'zgardi</option>
                <option>Boshqa shifokorga yozildim</option>
                <option>O'zimni yaxshi his qilyapman</option>
                <option>Vaqt to'g'ri kelmadi</option>
                <option>Boshqa sabab</option>
              </Select>
            </div>
          </div>
        )
      )}
    </Modal>
  )
}

function RescheduleModal({ open, onClose, b, onDone }: { open: boolean; onClose: () => void; b: Booking; onDone: () => void }) {
  const toast = useToast()
  const [date, setDate] = useState(b.start_at ? dayKey(b.start_at) : todayISO())
  const [slot, setSlot] = useState<PlacedSlot | null>(null)
  const m = useMutation({
    mutationFn: () => api<Booking>(`/booking/bookings/${b.id}/reschedule`, { method: 'POST', body: { new_slot_id: slot!.id } }),
    onSuccess: () => {
      toast.success("Vaqt o'zgartirildi")
      onDone()
      onClose()
    },
    onError: (e) => toast.error(e),
  })
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Yangi vaqtni tanlang"
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Bekor
          </Button>
          <Button disabled={!slot} loading={m.isPending} onClick={() => m.mutate()}>
            {slot ? `${relativeDateTime(slot.start_at)} ga ko'chirish` : "Vaqtni o'zgartirish"}
          </Button>
        </>
      }
    >
      {b.doctor_id && open && <SlotPicker doctorId={b.doctor_id} date={date} onDate={setDate} selected={slot?.id} onSelect={setSlot} place={b.place === 'home' ? 'home' : 'clinic'} />}
      <p className="mt-3 text-xs text-ink-400">Narx o'zgarmaydi. Vaqtni ko'chirish soni cheklangan bo'lishi mumkin.</p>
    </Modal>
  )
}

function ReviewModal({ open, onClose, b }: { open: boolean; onClose: () => void; b: Booking }) {
  const toast = useToast()
  const [rating, setRating] = useState(5)
  const [comment, setComment] = useState('')
  const [anon, setAnon] = useState(false)
  const m = useMutation({
    mutationFn: () => api<{ status: string }>(`/booking/bookings/${b.id}/review`, { method: 'POST', body: { rating, comment, is_anonymous: anon } }),
    onSuccess: (r) => {
      toast.success(r.status === 'pending' ? "Rahmat! Sharhingiz moderatsiyadan so'ng chiqadi" : 'Rahmat! Sharhingiz e‘lon qilindi')
      onClose()
    },
    onError: (e) => toast.error(e),
  })
  const labels = ['', 'Juda yomon', 'Yomon', "O'rtacha", 'Yaxshi', "A'lo!"]
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Qabul qanday o'tdi?"
      size="sm"
      footer={
        <Button block loading={m.isPending} onClick={() => m.mutate()}>
          Yuborish
        </Button>
      }
    >
      <div className="flex flex-col items-center py-2">
        <Stars value={rating} size={36} onChange={setRating} />
        <div className="mt-2 font-semibold text-ink-700">{labels[rating]}</div>
      </div>
      <Textarea className="mt-4" rows={4} maxLength={2000} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Boshqa bemorlarga foydali bo'lishi uchun tajribangiz bilan bo'lishing..." />
      <label className="mt-4 flex items-center justify-between rounded-2xl bg-ink-50 p-4">
        <span className="text-sm font-semibold text-ink-700">Anonim qoldirish</span>
        <Toggle checked={anon} onChange={setAnon} />
      </label>
      <p className="mt-3 text-xs text-ink-400">Sharhni qabuldan keyin 30 kun ichida yozish mumkin. Telefon raqam va havolalar avtomatik tekshiriladi.</p>
    </Modal>
  )
}

function DisputeModal({ open, onClose, b }: { open: boolean; onClose: () => void; b: Booking }) {
  const toast = useToast()
  const [reason, setReason] = useState('poor_service')
  const [description, setDescription] = useState('')
  const m = useMutation({
    mutationFn: () => api<{ due_at: string }>(`/booking/bookings/${b.id}/disputes`, { method: 'POST', body: { reason, description } }),
    onSuccess: (d) => {
      toast.success(`Murojaatingiz qabul qilindi. ${dateLong(d.due_at)} gacha ko'rib chiqiladi`)
      onClose()
    },
    onError: (e) => toast.error(e),
  })
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Muammo haqida xabar berish"
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Bekor
          </Button>
          <Button loading={m.isPending} onClick={() => m.mutate()}>
            Yuborish
          </Button>
        </>
      }
    >
      <p className="text-sm text-ink-500">Murojaatingizni moderator ko'rib chiqadi va kerak bo'lsa pulni qaytaradi.</p>
      <div className="mt-4 space-y-2">
        {[
          ['doctor_no_show', 'Shifokor kelmadi'],
          ['poor_service', 'Xizmat sifati past'],
          ['wrong_charge', "Noto'g'ri summa yechildi"],
          ['other', 'Boshqa'],
        ].map(([v, l]) => (
          <button key={v} onClick={() => setReason(v)} className={cx('flex w-full items-center gap-3 rounded-2xl border-2 p-3.5 text-left font-semibold transition', reason === v ? 'border-brand-500 bg-brand-50/50 text-ink-900' : 'border-ink-100 text-ink-600')}>
            <span className={cx('h-4 w-4 rounded-full border-2', reason === v ? 'border-[5px] border-brand-500' : 'border-ink-300')} />
            {l}
          </button>
        ))}
      </div>
      <Textarea className="mt-4" rows={3} maxLength={4000} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Batafsil yozing..." />
      <Badge tone="blue" className="mt-3">
        Odatda 3 ish kuni ichida javob beramiz
      </Badge>
    </Modal>
  )
}
