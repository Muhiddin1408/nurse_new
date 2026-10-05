import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'framer-motion'
import {
  ArrowLeft, ArrowRight, Building2, CalendarClock, Check, CircleAlert, Clock, House, LocateFixed, MapPin, Plus, Tag, TicketPercent, UserRound,
} from 'lucide-react'
import { api, ApiError, uuid } from '@/lib/api'
import { ageFrom, dateLong, minutes, money, relativeDateTime, sumMoney, time, todayISO, weekday } from '@/lib/format'
import { useAddresses, useDoctor, useDoctorServices, usePatients } from '@/lib/queries'
import { useToast } from '@/lib/toast'
import type { Address, Booking, Patient } from '@/lib/types'
import { SlotPicker, slotDay, useDaySlots, useFirstFreeDay, type Place, type PlacedSlot } from '@/components/SlotPicker'
import { AddressForm, PatientForm } from '@/components/forms'
import { Avatar, Button, ErrorState, Input, Modal, Money, Skeleton, Textarea, cx } from '@/components/ui'

const STEPS = ['Xizmat', 'Vaqt', 'Bemor', 'Tasdiqlash'] as const

interface Preview {
  original_price: string
  price_adjust_percent: number
  discount_amount: string
  discount_kind: string
  total_price: string
}

const DISCOUNT_LABEL: Record<string, string> = {
  promo: 'Promo kod',
  first_booking: 'Birinchi bron chegirmasi',
  follow_up: 'Takroriy qabul chegirmasi',
  package: 'Paket chegirmasi',
}

export default function BookingWizard() {
  const { doctorId = '' } = useParams()
  const [sp] = useSearchParams()
  const nav = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()

  const doctor = useDoctor(doctorId)
  const allServices = useDoctorServices(doctorId)
  const patients = usePatients()
  const addresses = useAddresses()

  const [step, setStep] = useState(0)
  const [place, setPlace] = useState<Place>((sp.get('place') as Place) || 'clinic')
  const [serviceIds, setServiceIds] = useState<string[]>([])
  const [date, setDate] = useState(sp.get('date') || todayISO())
  const [slot, setSlot] = useState<PlacedSlot | null>(null)
  const [patientId, setPatientId] = useState<string>('')
  const [addressId, setAddressId] = useState<string>('')
  const [comment, setComment] = useState('')
  const [promo, setPromo] = useState('')
  const [appliedPromo, setAppliedPromo] = useState('')
  const [taken, setTaken] = useState<{ detail: string; alternatives: { slot_id: string; start_at: string }[] } | null>(null)
  const [addPatient, setAddPatient] = useState(false)
  const [addAddress, setAddAddress] = useState(false)

  const services = useMemo(() => allServices.data?.filter((s) => s.place === place) ?? [], [allServices.data, place])
  const hasHome = !!allServices.data?.some((s) => s.place === 'home')
  const hasClinic = !!allServices.data?.some((s) => s.place === 'clinic')
  const chosen = services.filter((s) => serviceIds.includes(s.id))
  const duration = chosen.reduce((m, s) => m + s.duration_minutes, 0)
  const subtotal = chosen.length ? sumMoney(chosen.map((s) => s.price)) : '0.00'
  const clinicNames = useMemo(() => Object.fromEntries((doctor.data?.clinics ?? []).map((c) => [c.id, c.name])), [doctor.data])

  // URL'dan kelgan slotni tiklash
  const preSlotId = sp.get('slot')
  const daySlots = useDaySlots(doctorId, sp.get('date') || '')
  useEffect(() => {
    if (preSlotId && !slot && daySlots.data) {
      const s = daySlots.data.find((x) => x.id === preSlotId)
      if (s) setSlot(s)
    }
  }, [preSlotId, daySlots.data, slot])

  const firstFree = useFirstFreeDay(doctorId, place)
  useEffect(() => {
    if (!sp.get('date') && firstFree.data) setDate(slotDay(firstFree.data))
  }, [firstFree.data, sp])

  useEffect(() => {
    if (allServices.data && !hasClinic && hasHome) setPlace('home')
  }, [allServices.data, hasClinic, hasHome])

  // Default tanlovlar
  useEffect(() => {
    if (services.length && !serviceIds.some((id) => services.some((s) => s.id === id))) setServiceIds([services[0].id])
  }, [services, serviceIds])
  useEffect(() => {
    if (!patientId && patients.data?.length) setPatientId((patients.data.find((p) => /o.?zim/i.test(p.relation)) ?? patients.data[0]).id)
  }, [patients.data, patientId])
  useEffect(() => {
    if (!addressId && addresses.data?.length) setAddressId((addresses.data.find((a) => a.is_default) ?? addresses.data[0]).id)
  }, [addresses.data, addressId])

  // Joy o'zgarsa — mos kelmaydigan slot tashlanadi
  useEffect(() => {
    if (slot && (place === 'home') !== !slot.clinic_id) setSlot(null)
  }, [place, slot])

  const slotMinutes = slot ? Math.round((new Date(slot.end_at).getTime() - new Date(slot.start_at).getTime()) / 60000) : null
  const tooLong = slotMinutes !== null && duration > slotMinutes

  const preview = useQuery({
    queryKey: ['price-preview', doctorId, serviceIds, slot?.id, appliedPromo],
    queryFn: () => api<Preview>('/booking/price-preview', { method: 'POST', body: { doctor_id: doctorId, service_ids: serviceIds, slot_id: slot?.id ?? null, promo_code: appliedPromo } }),
    enabled: step === 3 && serviceIds.length > 0,
    retry: false,
  })

  const payload = useMemo(
    () => ({
      patient_id: patientId,
      doctor_id: doctorId,
      slot_id: slot?.id,
      service_ids: serviceIds,
      address_id: place === 'home' ? addressId || null : null,
      comment,
      promo_code: appliedPromo,
    }),
    [patientId, doctorId, slot, serviceIds, place, addressId, comment, appliedPromo],
  )
  // Idempotency-Key: bir xil so'rov qayta yuborilsa (tarmoq uzilishi) — o'sha kalit; tarkib o'zgarsa — yangi kalit
  const idemKey = useMemo(() => uuid(), [JSON.stringify(payload)]) // eslint-disable-line react-hooks/exhaustive-deps

  const create = useMutation({
    mutationFn: () => api<Booking>('/booking/bookings', { method: 'POST', body: payload, headers: { 'Idempotency-Key': idemKey } }),
    onSuccess: (b) => {
      qc.invalidateQueries({ queryKey: ['bookings'] })
      qc.invalidateQueries({ queryKey: ['slots', doctorId] })
      nav(`/account/bookings/${b.id}?new=1`, { replace: true })
    },
    onError: (e) => {
      const err = e as ApiError
      if (err.status === 409 && (err.data as { code?: string })?.code === 'slot_taken') {
        qc.invalidateQueries({ queryKey: ['slots', doctorId] })
        setTaken({ detail: err.detail, alternatives: ((err.data as { alternatives?: [] }).alternatives ?? []) as { slot_id: string; start_at: string }[] })
        return
      }
      toast.error(err)
    },
  })

  const promoError = preview.error instanceof ApiError && appliedPromo ? preview.error.detail : ''

  const canNext = [
    serviceIds.length > 0,
    !!slot && !tooLong,
    !!patientId && (place !== 'home' || !!addressId),
    !!slot && !!patientId && !promoError,
  ][step]

  const patient = patients.data?.find((p) => p.id === patientId)
  const address = addresses.data?.find((a) => a.id === addressId)

  if (doctor.isError) return <div className="container-x py-12"><div className="card"><ErrorState error={doctor.error} onRetry={() => doctor.refetch()} /></div></div>

  return (
    <div className="mesh-bg -mt-[72px] min-h-screen pb-32 pt-[92px]">
      <div className="container-x max-w-6xl">
        <Link to={`/doctors/${doctorId}`} className="mb-4 inline-flex items-center gap-1.5 text-sm font-semibold text-ink-500 hover:text-ink-900">
          <ArrowLeft className="h-4 w-4" /> Shifokor sahifasi
        </Link>
        <h1 className="text-2xl font-extrabold text-ink-950 sm:text-3xl">Qabulga yozilish</h1>

        {/* Stepper */}
        <div className="mt-6 flex items-center gap-2">
          {STEPS.map((s, i) => (
            <button key={s} disabled={i > step} onClick={() => setStep(i)} className="group flex flex-1 flex-col gap-2 text-left">
              <div className={cx('h-1.5 rounded-full transition-all', i <= step ? 'bg-brand-500' : 'bg-ink-200')} />
              <span className={cx('hidden text-sm font-semibold sm:block', i === step ? 'text-ink-900' : i < step ? 'text-brand-700' : 'text-ink-400')}>
                {i + 1}. {s}
              </span>
            </button>
          ))}
        </div>
        <div className="mt-2 text-sm font-semibold text-ink-700 sm:hidden">
          {step + 1}/{STEPS.length} · {STEPS[step]}
        </div>

        <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-[1fr_360px]">
          <div className="card min-w-0 p-5 sm:p-7">
            <AnimatePresence mode="wait">
              <motion.div key={step} initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -16 }} transition={{ duration: 0.18 }}>
                {step === 0 && (
                  <div>
                    <StepTitle title="Qayerda va qanday xizmat?" text="Bir nechta xizmatni birga tanlashingiz mumkin." />
                    {hasHome && hasClinic && (
                      <div className="mb-6 grid grid-cols-2 gap-3">
                        {(['clinic', 'home'] as Place[]).map((p) => (
                          <button
                            key={p}
                            onClick={() => {
                              setPlace(p)
                              setServiceIds([])
                            }}
                            className={cx('flex items-center gap-3 rounded-2xl border-2 p-4 text-left transition', place === p ? 'border-brand-500 bg-brand-50/60' : 'border-ink-100 hover:border-ink-200')}
                          >
                            <span className={cx('flex h-11 w-11 items-center justify-center rounded-xl', place === p ? 'bg-brand-500 text-white' : 'bg-ink-100 text-ink-500')}>
                              {p === 'clinic' ? <Building2 className="h-5 w-5" /> : <House className="h-5 w-5" />}
                            </span>
                            <span>
                              <span className="block font-bold text-ink-900">{p === 'clinic' ? 'Klinikada' : 'Uyga chaqiruv'}</span>
                              <span className="block text-xs text-ink-500">{p === 'clinic' ? 'Shifokor qabuliga borasiz' : 'Shifokor sizga keladi'}</span>
                            </span>
                          </button>
                        ))}
                      </div>
                    )}
                    {allServices.isLoading ? (
                      <div className="space-y-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-20 rounded-2xl" />)}</div>
                    ) : (
                      <div className="space-y-3">
                        {services.map((s) => {
                          const on = serviceIds.includes(s.id)
                          return (
                            <button
                              key={s.id}
                              onClick={() => setServiceIds((ids) => (on ? ids.filter((x) => x !== s.id) : [...ids, s.id]))}
                              className={cx('flex w-full items-center gap-4 rounded-2xl border-2 p-4 text-left transition', on ? 'border-brand-500 bg-brand-50/50' : 'border-ink-100 hover:border-ink-200')}
                            >
                              <span className={cx('flex h-6 w-6 shrink-0 items-center justify-center rounded-lg border-2 transition', on ? 'border-brand-500 bg-brand-500 text-white' : 'border-ink-300')}>
                                {on && <Check className="h-4 w-4" strokeWidth={3} />}
                              </span>
                              <span className="flex-1">
                                <span className="block font-semibold text-ink-900">{s.name}</span>
                                <span className="mt-0.5 flex items-center gap-1 text-sm text-ink-500">
                                  <Clock className="h-3.5 w-3.5" /> {minutes(s.duration_minutes)}
                                </span>
                              </span>
                              <Money value={s.price} className="font-bold text-ink-900" />
                            </button>
                          )
                        })}
                      </div>
                    )}
                  </div>
                )}

                {step === 1 && (
                  <div>
                    <StepTitle title="Qulay vaqtni tanlang" text={place === 'home' ? 'Shifokor tanlangan vaqtda manzilingizga keladi.' : "Faqat haqiqatan bo'sh vaqtlar ko'rsatilgan."} />
                    <SlotPicker doctorId={doctorId} date={date} onDate={setDate} selected={slot?.id} onSelect={setSlot} place={place} clinicNames={clinicNames} />
                    {tooLong && (
                      <div className="mt-4 flex items-start gap-3 rounded-2xl bg-amber-50 p-4 text-sm text-amber-800 ring-1 ring-amber-100">
                        <CircleAlert className="mt-0.5 h-5 w-5 shrink-0" />
                        Tanlangan xizmatlar {minutes(duration)} davom etadi, bu vaqt oralig'i esa {minutes(slotMinutes!)}. Xizmatlar sonini kamaytiring yoki boshqa vaqt tanlang.
                      </div>
                    )}
                  </div>
                )}

                {step === 2 && (
                  <div>
                    <StepTitle title="Kim uchun?" text="O'zingiz yoki oila a'zolaringizdan birini tanlang." />
                    {patients.isLoading ? (
                      <Skeleton className="h-20 rounded-2xl" />
                    ) : (
                      <div className="grid gap-3 sm:grid-cols-2">
                        {patients.data?.map((p) => (
                          <PatientCard key={p.id} p={p} active={p.id === patientId} onClick={() => setPatientId(p.id)} />
                        ))}
                        <button onClick={() => setAddPatient(true)} className="flex items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-ink-200 p-4 font-semibold text-ink-500 transition hover:border-brand-400 hover:text-brand-700">
                          <Plus className="h-5 w-5" /> Bemor qo'shish
                        </button>
                      </div>
                    )}

                    {place === 'home' && (
                      <div className="mt-8">
                        <h3 className="mb-3 font-bold text-ink-900">Manzil</h3>
                        <div className="space-y-3">
                          {addresses.data?.map((a) => (
                            <AddressCard key={a.id} a={a} active={a.id === addressId} onClick={() => setAddressId(a.id)} />
                          ))}
                          <button onClick={() => setAddAddress(true)} className="flex w-full items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-ink-200 p-4 font-semibold text-ink-500 transition hover:border-brand-400 hover:text-brand-700">
                            <LocateFixed className="h-5 w-5" /> Yangi manzil
                          </button>
                        </div>
                      </div>
                    )}

                    <div className="mt-8">
                      <span className="label">Shifokorga izoh (ixtiyoriy)</span>
                      <Textarea value={comment} onChange={(e) => setComment(e.target.value)} maxLength={1000} placeholder="Shikoyatlaringiz, allergiya, oldingi tashxislar..." />
                    </div>
                  </div>
                )}

                {step === 3 && (
                  <div>
                    <StepTitle title="Hammasi to'g'rimi?" text="Bron qilishdan oldin ma'lumotlarni tekshiring." />
                    <div className="divide-y divide-ink-100 rounded-2xl border border-ink-100">
                      <SummaryRow icon={<CalendarClock className="h-5 w-5" />} label="Vaqt" value={slot ? `${weekday(slotDay(slot))}, ${dateLong(slot.start_at)} · ${time(slot.start_at)}–${time(slot.end_at)}` : '—'} onEdit={() => setStep(1)} />
                      <SummaryRow
                        icon={place === 'home' ? <House className="h-5 w-5" /> : <MapPin className="h-5 w-5" />}
                        label={place === 'home' ? 'Manzil (uyga chaqiruv)' : 'Klinika'}
                        value={place === 'home' ? (address ? `${address.city}, ${address.street}${address.apartment ? ', kv. ' + address.apartment : ''}` : '—') : slot?.clinic_id ? clinicNames[slot.clinic_id] ?? 'Klinika' : '—'}
                        onEdit={() => setStep(place === 'home' ? 2 : 1)}
                      />
                      <SummaryRow icon={<UserRound className="h-5 w-5" />} label="Bemor" value={patient ? `${patient.full_name} · ${ageFrom(patient.birth_date)} yosh` : '—'} onEdit={() => setStep(2)} />
                    </div>

                    <div className="mt-6">
                      <span className="label">Promo kod</span>
                      <div className="flex gap-2">
                        <div className="relative flex-1">
                          <TicketPercent className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-ink-400" />
                          <Input value={promo} onChange={(e) => setPromo(e.target.value.toUpperCase())} placeholder="KOD" className="pl-12 font-semibold uppercase tracking-wider" invalid={!!promoError} />
                        </div>
                        {appliedPromo ? (
                          <Button variant="outline" onClick={() => { setAppliedPromo(''); setPromo('') }}>Olib tashlash</Button>
                        ) : (
                          <Button variant="secondary" disabled={!promo.trim()} onClick={() => setAppliedPromo(promo.trim())}>Qo'llash</Button>
                        )}
                      </div>
                      {promoError && <p className="mt-1.5 text-sm font-medium text-rose-600">{promoError}</p>}
                      {appliedPromo && preview.data && preview.data.discount_kind === 'promo' && <p className="mt-1.5 text-sm font-medium text-brand-700">Promo kod qo'llandi ✓</p>}
                    </div>

                    <PaymentNote place={place} />
                  </div>
                )}
              </motion.div>
            </AnimatePresence>
          </div>

          {/* Xulosa */}
          <aside className="min-w-0 lg:sticky lg:top-24 lg:self-start">
            <div className="card overflow-hidden">
              <div className="flex items-center gap-3 border-b border-ink-100 p-5">
                {doctor.data ? <Avatar name={doctor.data.full_name.replace(/^Dr\.?\s*/, '')} size={52} /> : <Skeleton className="h-13 w-13 rounded-2xl" />}
                <div className="min-w-0">
                  <div className="truncate font-bold text-ink-900">{doctor.data?.full_name ?? '...'}</div>
                  <div className="truncate text-sm text-brand-700">{doctor.data?.specializations.join(', ')}</div>
                </div>
              </div>
              <div className="space-y-3 p-5 text-sm">
                {slot && (
                  <div className="flex items-center gap-2 rounded-xl bg-brand-50 px-3 py-2.5 font-semibold text-brand-800">
                    <CalendarClock className="h-4 w-4" /> {relativeDateTime(slot.start_at)}
                  </div>
                )}
                {chosen.map((s) => (
                  <div key={s.id} className="flex justify-between gap-3">
                    <span className="text-ink-600">{s.name}</span>
                    <Money value={s.price} className="shrink-0 font-semibold text-ink-900" />
                  </div>
                ))}
                {step === 3 && preview.data ? (
                  <>
                    {preview.data.price_adjust_percent !== 0 && (
                      <div className="flex justify-between text-ink-500">
                        <span>Vaqt bo'yicha narx ({preview.data.price_adjust_percent > 0 ? '+' : ''}{preview.data.price_adjust_percent}%)</span>
                      </div>
                    )}
                    {Number(preview.data.discount_amount) > 0 && (
                      <div className="flex justify-between font-semibold text-brand-700">
                        <span className="flex items-center gap-1.5"><Tag className="h-4 w-4" /> {DISCOUNT_LABEL[preview.data.discount_kind] ?? 'Chegirma'}</span>
                        <span>−{money(preview.data.discount_amount)}</span>
                      </div>
                    )}
                    <div className="flex items-end justify-between border-t border-ink-100 pt-3">
                      <span className="font-semibold text-ink-700">Jami</span>
                      <span className="text-right">
                        {preview.data.original_price !== preview.data.total_price && <Money value={preview.data.original_price} strike className="mr-2 text-sm" />}
                        <Money value={preview.data.total_price} className="text-2xl font-extrabold text-ink-950" />
                      </span>
                    </div>
                  </>
                ) : (
                  <div className="flex items-end justify-between border-t border-ink-100 pt-3">
                    <span className="font-semibold text-ink-700">Jami</span>
                    {step === 3 && preview.isLoading ? <Skeleton className="h-7 w-28" /> : <Money value={subtotal} className="text-2xl font-extrabold text-ink-950" />}
                  </div>
                )}
                {duration > 0 && <div className="text-xs text-ink-400">Davomiyligi: {minutes(duration)}</div>}
              </div>
            </div>
          </aside>
        </div>
      </div>

      {/* Pastki navigatsiya */}
      <div className="fixed inset-x-0 bottom-0 z-40 border-t border-ink-100 bg-white/95 backdrop-blur-xl">
        <div className="container-x flex max-w-6xl items-center justify-between gap-3 py-3">
          <Button variant="ghost" onClick={() => (step === 0 ? nav(-1) : setStep(step - 1))} icon={<ArrowLeft className="h-4 w-4" />}>
            Orqaga
          </Button>
          {step < 3 ? (
            <Button size="lg" disabled={!canNext} onClick={() => setStep(step + 1)} className="min-w-[160px] flex-row-reverse" icon={<ArrowRight className="h-5 w-5" />}>
              Davom etish
            </Button>
          ) : (
            <Button size="lg" disabled={!canNext} loading={create.isPending} onClick={() => create.mutate()} className="min-w-[180px]">
              Bron qilish
            </Button>
          )}
        </div>
      </div>

      <Modal open={addPatient} onClose={() => setAddPatient(false)} title="Yangi bemor">
        <PatientForm
          onSaved={(p) => {
            setPatientId(p.id)
            setAddPatient(false)
          }}
        />
      </Modal>
      <Modal open={addAddress} onClose={() => setAddAddress(false)} title="Yangi manzil">
        <AddressForm
          onSaved={(a) => {
            setAddressId(a.id)
            setAddAddress(false)
          }}
        />
      </Modal>
      <Modal open={!!taken} onClose={() => setTaken(null)} title="Bu vaqt hozirgina band bo'ldi" size="sm">
        <p className="text-ink-600">Kimdir sizdan bir necha soniya oldin shu vaqtni band qildi. Mana eng yaqin bo'sh vaqtlar:</p>
        <div className="mt-4 grid grid-cols-2 gap-2">
          {taken?.alternatives.map((a) => (
            <button
              key={a.slot_id}
              onClick={async () => {
                const day = slotDay({ id: a.slot_id, start_at: a.start_at, end_at: a.start_at })
                setDate(day)
                const rows = await qc.fetchQuery({ queryKey: ['slots', doctorId, day], queryFn: () => api<PlacedSlot[]>(`/schedule/doctors/${doctorId}/slots`, { auth: false, query: { date: day } }) })
                const s = rows.find((x) => x.id === a.slot_id)
                if (s) setSlot(s)
                setTaken(null)
              }}
              className="rounded-2xl bg-brand-50 p-3 text-left font-semibold text-brand-800 hover:bg-brand-100"
            >
              {relativeDateTime(a.start_at)}
            </button>
          ))}
        </div>
        <Button
          variant="outline"
          block
          className="mt-4"
          onClick={() => {
            setSlot(null)
            setTaken(null)
            setStep(1)
          }}
        >
          Boshqa vaqt tanlash
        </Button>
      </Modal>
    </div>
  )
}

function StepTitle({ title, text }: { title: string; text?: string }) {
  return (
    <div className="mb-6">
      <h2 className="text-xl font-bold text-ink-900">{title}</h2>
      {text && <p className="mt-1 text-ink-500">{text}</p>}
    </div>
  )
}

function PatientCard({ p, active, onClick }: { p: Patient; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={cx('flex items-center gap-3 rounded-2xl border-2 p-4 text-left transition', active ? 'border-brand-500 bg-brand-50/50' : 'border-ink-100 hover:border-ink-200')}>
      <Avatar name={p.full_name} size={44} className="rounded-xl" />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-semibold text-ink-900">{p.full_name}</span>
        <span className="block text-sm text-ink-500">
          {p.relation || 'Bemor'} · {ageFrom(p.birth_date)} yosh
        </span>
      </span>
      {active && <Check className="h-5 w-5 text-brand-600" />}
    </button>
  )
}

function AddressCard({ a, active, onClick }: { a: Address; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={cx('flex w-full items-start gap-3 rounded-2xl border-2 p-4 text-left transition', active ? 'border-brand-500 bg-brand-50/50' : 'border-ink-100 hover:border-ink-200')}>
      <span className={cx('flex h-10 w-10 shrink-0 items-center justify-center rounded-xl', active ? 'bg-brand-500 text-white' : 'bg-ink-100 text-ink-500')}>
        <MapPin className="h-5 w-5" />
      </span>
      <span className="flex-1">
        <span className="block font-semibold text-ink-900">{a.label || 'Manzil'}</span>
        <span className="block text-sm text-ink-500">
          {a.city}, {a.street}
          {a.apartment && `, kv. ${a.apartment}`}
        </span>
      </span>
      {active && <Check className="h-5 w-5 text-brand-600" />}
    </button>
  )
}

function SummaryRow({ icon, label, value, onEdit }: { icon: React.ReactNode; label: string; value: string; onEdit: () => void }) {
  return (
    <div className="flex items-center gap-4 p-4">
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-ink-50 text-ink-500">{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="text-xs font-semibold uppercase tracking-wide text-ink-400">{label}</div>
        <div className="font-semibold text-ink-900">{value}</div>
      </div>
      <button onClick={onEdit} className="text-sm font-semibold text-brand-600 hover:text-brand-700">
        O'zgartirish
      </button>
    </div>
  )
}

function PaymentNote({ place }: { place: Place }) {
  return (
    <div className="mt-6 rounded-2xl bg-ink-50 p-4 text-sm text-ink-600">
      <div className="font-semibold text-ink-900">To'lov haqida</div>
      <p className="mt-1">
        {place === 'home'
          ? "Uyga chaqiruv oldindan to'lanadi (Payme yoki Click). To'lov uchun 10 daqiqa vaqt beriladi — shu vaqt ichida vaqt siz uchun saqlanadi."
          : "Shifokor sozlamasiga qarab to'lov klinikada yoki onlayn (Payme / Click) amalga oshiriladi. Keyingi qadamda aniq ko'rsatamiz."}
      </p>
      <p className="mt-2 text-xs text-ink-500">Qabuldan 24 soatdan ko'proq oldin bekor qilsangiz — to'liq qaytariladi.</p>
    </div>
  )
}
