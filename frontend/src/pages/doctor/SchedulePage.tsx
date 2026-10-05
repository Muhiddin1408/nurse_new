import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { fromZonedTime } from 'date-fns-tz'
import { motion } from 'framer-motion'
import { CalendarOff, ChevronLeft, ChevronRight, Home, Lock, LockOpen, MapPin, Pencil, Plane, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { useToast } from '@/lib/toast'
import { TZ, WEEKDAYS, addDaysISO, dateLong, dateShort, dayKey, relativeDateTime, time, todayISO, weekdayShort } from '@/lib/format'
import { Badge, Button, ConfirmDialog, EmptyState, ErrorState, Field, Input, Modal, PageHeader, Segmented, Select, Skeleton, Textarea, cx } from '@/components/ui'
import { ConflictModal, SLOT_MINUTES, SectionCard, getConflict, hhmm, useMyClinics, type Conflict, type ScheduleSlot } from './shared'

interface WorkingRule {
  id: string
  weekday: number
  start_time: string
  end_time: string
  slot_minutes: number
  valid_from: string
  valid_to: string | null
  clinic_id: string | null
}

interface TimeOff {
  id: string
  start_at: string
  end_at: string
  kind: 'vacation' | 'sick' | 'personal' | 'other'
  reason: string
}

type ConflictState = { detail: string; conflicts: Conflict[]; options: string[] }

const TIMEOFF_KIND: Record<string, string> = { vacation: "Ta'til", sick: 'Kasallik', personal: 'Shaxsiy', other: 'Boshqa' }

const SLOT_STYLE: Record<string, string> = {
  free: 'bg-brand-50 text-brand-800 ring-brand-200 hover:bg-brand-100',
  held: 'bg-amber-50 text-amber-800 ring-amber-200',
  booked: 'bg-sky-500 text-white ring-sky-500 shadow-sm',
  blocked: 'bg-ink-100 text-ink-400 ring-ink-200 line-through decoration-ink-300',
}

const BLOCK_REASON: Record<string, string> = {
  manual: "Qo'lda yopilgan",
  time_off: "Ta'til / dam olish",
  travel_buffer: "Yo'l vaqti (uy chaqiruvi)",
  clinic_closed: 'Klinika yopiq',
}

function mondayOf(iso: string) {
  const d = new Date(iso + 'T12:00:00Z')
  return addDaysISO(iso, -((d.getUTCDay() + 6) % 7))
}

export default function SchedulePage() {
  const [tab, setTab] = useState<'calendar' | 'rules' | 'timeoff'>('calendar')
  const [needsRegen, setNeedsRegen] = useState(false)
  const qc = useQueryClient()
  const toast = useToast()

  const regen = useMutation({
    mutationFn: () => api<{ removed: number; created: number }>('/doctor/schedule/regenerate', { method: 'POST' }),
    onSuccess: (r) => {
      toast.success(`Jadval yangilandi: ${r.created} ta yangi slot, ${r.removed} ta olib tashlandi`)
      setNeedsRegen(false)
      qc.invalidateQueries({ queryKey: ['doctor', 'schedule'] })
    },
    onError: (e) => toast.error(e),
  })

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Jadval"
        subtitle="Ish vaqtingiz, bo'sh va band slotlar, dam olish kunlari"
        actions={
          <Button variant="outline" icon={<RefreshCw className={cx('h-4 w-4', regen.isPending && 'animate-spin')} />} loading={regen.isPending} onClick={() => regen.mutate()}>
            Slotlarni qayta yaratish
          </Button>
        }
      />

      {needsRegen && (
        <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} className="mb-5 flex flex-col gap-3 rounded-3xl bg-gradient-to-r from-amber-50 to-orange-50 p-4 ring-1 ring-amber-200 sm:flex-row sm:items-center sm:justify-between">
          <div className="text-sm text-amber-900">
            <b>Ish qoidalari o'zgardi.</b> Slotlar avtomatik yangilanmaydi — mijozlar yangi vaqtlarni ko'rishi uchun jadvalni yangilang.
          </div>
          <Button size="sm" loading={regen.isPending} onClick={() => regen.mutate()} icon={<RefreshCw className="h-4 w-4" />}>
            Jadvalni yangilash
          </Button>
        </motion.div>
      )}

      <div className="scrollbar-none mb-5 overflow-x-auto">
        <Segmented
          value={tab}
          onChange={setTab}
          options={[
            { value: 'calendar', label: 'Haftalik jadval' },
            { value: 'rules', label: 'Ish qoidalari' },
            { value: 'timeoff', label: "Ta'til va dam" },
          ]}
        />
      </div>

      {tab === 'calendar' && <WeekCalendar />}
      {tab === 'rules' && <RulesTab onChanged={() => setNeedsRegen(true)} />}
      {tab === 'timeoff' && <TimeOffTab />}
    </div>
  )
}

// ---------------------------------------------------------------------------
function WeekCalendar() {
  const [monday, setMonday] = useState(() => mondayOf(todayISO()))
  const [sel, setSel] = useState<ScheduleSlot | null>(null)
  const [note, setNote] = useState('')
  const qc = useQueryClient()
  const toast = useToast()
  const to = addDaysISO(monday, 6)

  const q = useQuery({
    queryKey: ['doctor', 'schedule', monday],
    queryFn: () => api<ScheduleSlot[]>('/doctor/schedule', { query: { from: monday, to } }),
  })

  const days = useMemo(() => {
    const out: { day: string; slots: ScheduleSlot[] }[] = []
    for (let i = 0; i < 7; i++) out.push({ day: addDaysISO(monday, i), slots: [] })
    for (const s of q.data ?? []) {
      const d = out.find((x) => x.day === dayKey(s.start_at))
      d?.slots.push(s)
    }
    out.forEach((d) => d.slots.sort((a, b) => a.start_at.localeCompare(b.start_at)))
    return out
  }, [q.data, monday])

  const counts = useMemo(() => {
    const c = { free: 0, booked: 0, blocked: 0, held: 0 } as Record<string, number>
    for (const s of q.data ?? []) c[s.status] = (c[s.status] ?? 0) + 1
    return c
  }, [q.data])

  const act = useMutation({
    mutationFn: ({ slot, action }: { slot: ScheduleSlot; action: 'block' | 'unblock' }) =>
      api(`/doctor/slots/${slot.slot_id}/${action}`, { method: 'POST', body: action === 'block' ? { note } : {} }),
    onSuccess: (_d, v) => {
      toast.success(v.action === 'block' ? 'Slot yopildi' : 'Slot qayta ochildi')
      setSel(null)
      setNote('')
      qc.invalidateQueries({ queryKey: ['doctor', 'schedule'] })
    },
    onError: (e) => toast.error(e),
  })

  const isPast = (s: ScheduleSlot) => Date.parse(s.start_at) < Date.now()

  return (
    <>
      <div className="card mb-4 flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setMonday(addDaysISO(monday, -7))} aria-label="Oldingi hafta">
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <div className="min-w-[150px] text-center text-sm font-bold">
            {dateShort(monday)} — {dateShort(to)}
          </div>
          <Button size="sm" variant="outline" onClick={() => setMonday(addDaysISO(monday, 7))} aria-label="Keyingi hafta">
            <ChevronRight className="h-4 w-4" />
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setMonday(mondayOf(todayISO()))}>
            Shu hafta
          </Button>
        </div>
        <div className="flex flex-wrap gap-3 text-xs font-semibold text-ink-600">
          <Legend className="bg-brand-100 ring-brand-300" label={`Bo'sh · ${counts.free ?? 0}`} />
          <Legend className="bg-sky-500 ring-sky-500" label={`Band · ${counts.booked ?? 0}`} />
          <Legend className="bg-amber-100 ring-amber-300" label={`Ushlab turilgan · ${counts.held ?? 0}`} />
          <Legend className="bg-ink-200 ring-ink-300" label={`Yopiq · ${counts.blocked ?? 0}`} />
        </div>
      </div>

      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      <div className="scrollbar-none -mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
        <div className="grid min-w-[980px] grid-cols-7 gap-3">
          {days.map(({ day, slots }) => {
            const isToday = day === todayISO()
            return (
              <div key={day} className={cx('card flex flex-col overflow-hidden', isToday && 'ring-2 ring-brand-400')}>
                <div className={cx('border-b border-ink-100 px-3 py-3 text-center', isToday ? 'bg-gradient-to-b from-brand-50 to-white' : 'bg-ink-50/50')}>
                  <div className={cx('text-xs font-bold uppercase tracking-wide', isToday ? 'text-brand-700' : 'text-ink-400')}>{weekdayShort(day)}</div>
                  <div className="text-lg font-extrabold text-ink-900">{dateShort(day)}</div>
                </div>
                <div className="flex-1 space-y-1.5 p-2">
                  {q.isLoading && Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-9" />)}
                  {q.data && slots.length === 0 && <div className="py-8 text-center text-xs text-ink-400">Ish kuni emas</div>}
                  {slots.map((s) => (
                    <button
                      key={s.slot_id}
                      onClick={() => setSel(s)}
                      disabled={isPast(s) && s.status === 'free'}
                      title={s.booking ? `${s.booking.patient} — ${s.booking.services.join(', ')}` : BLOCK_REASON[s.block_reason] || ''}
                      className={cx(
                        'flex w-full items-center justify-between gap-1 rounded-xl px-2.5 py-2 text-left text-xs font-bold ring-1 ring-inset transition disabled:cursor-default',
                        SLOT_STYLE[s.status] ?? SLOT_STYLE.free,
                        isPast(s) && 'opacity-50',
                      )}
                    >
                      <span className="tabular-nums">{time(s.start_at)}</span>
                      {s.place === 'home' ? <Home className="h-3 w-3 opacity-70" /> : s.status === 'blocked' ? <Lock className="h-3 w-3" /> : null}
                      {s.booking && <span className="truncate font-semibold opacity-90">{s.booking.patient.split(' ')[0]}</span>}
                    </button>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      <Modal
        open={!!sel}
        onClose={() => {
          setSel(null)
          setNote('')
        }}
        title={sel ? `${relativeDateTime(sel.start_at)} – ${time(sel.end_at)}` : ''}
        size="sm"
        footer={
          sel && sel.status === 'free' ? (
            <Button variant="dark" icon={<Lock className="h-4 w-4" />} loading={act.isPending} onClick={() => act.mutate({ slot: sel, action: 'block' })}>
              Slotni yopish
            </Button>
          ) : sel && sel.status === 'blocked' && sel.block_reason === 'manual' && !isPast(sel) ? (
            <Button icon={<LockOpen className="h-4 w-4" />} loading={act.isPending} onClick={() => act.mutate({ slot: sel, action: 'unblock' })}>
              Qayta ochish
            </Button>
          ) : undefined
        }
      >
        {sel && (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2">
              <Badge tone={sel.status === 'free' ? 'green' : sel.status === 'booked' ? 'blue' : sel.status === 'held' ? 'amber' : 'gray'} dot>
                {{ free: "Bo'sh", booked: 'Band', held: "To'lov kutilmoqda", blocked: 'Yopiq' }[sel.status] ?? sel.status}
              </Badge>
              <Badge tone={sel.place === 'home' ? 'violet' : 'blue'}>
                {sel.place === 'home' ? <Home className="h-3 w-3" /> : <MapPin className="h-3 w-3" />}
                {sel.place === 'home' ? 'Uyga chaqiruv' : sel.clinic}
              </Badge>
            </div>
            {sel.booking && (
              <div className="rounded-2xl bg-sky-50 p-4">
                <div className="font-bold text-ink-900">{sel.booking.patient}</div>
                <div className="text-sm text-ink-600">{sel.booking.services.join(', ')}</div>
                <div className="mt-1 text-xs text-ink-500">Bron #{sel.booking.number}</div>
              </div>
            )}
            {sel.status === 'blocked' && (
              <div className="rounded-2xl bg-ink-50 p-4 text-sm text-ink-700">
                <b>{BLOCK_REASON[sel.block_reason] || 'Yopiq'}</b>
                {sel.block_note && <div className="mt-1 text-ink-500">{sel.block_note}</div>}
                {sel.block_reason !== 'manual' && <div className="mt-1 text-xs text-ink-400">Bu slot avtomatik yopilgan, uni bu yerdan ochib bo'lmaydi.</div>}
              </div>
            )}
            {sel.status === 'free' && (
              <Field label="Izoh (ixtiyoriy)" hint="Yopilgan slotga mijozlar yozila olmaydi.">
                <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Masalan: tushlik, majlis" />
              </Field>
            )}
          </div>
        )}
      </Modal>
    </>
  )
}

function Legend({ className, label }: { className: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className={cx('h-3 w-3 rounded ring-1 ring-inset', className)} />
      {label}
    </span>
  )
}

// ---------------------------------------------------------------------------
const emptyRule = { weekday: 0, start_time: '09:00', end_time: '17:00', slot_minutes: 30, valid_from: todayISO(), valid_to: '', clinic_id: '' }

function RulesTab({ onChanged }: { onChanged: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const clinicsQ = useMyClinics()
  const clinics = clinicsQ.data?.clinics ?? []
  const q = useQuery({ queryKey: ['doctor', 'working-rules'], queryFn: () => api<WorkingRule[]>('/doctor/working-rules') })

  const [edit, setEdit] = useState<(typeof emptyRule & { id?: string }) | null>(null)
  const [del, setDel] = useState<WorkingRule | null>(null)
  const [conflict, setConflict] = useState<ConflictState | null>(null)
  const [retry, setRetry] = useState<null | { kind: 'save' } | { kind: 'remove'; rule: WorkingRule }>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})

  const done = (msg: string) => {
    toast.success(msg)
    setEdit(null)
    setDel(null)
    setConflict(null)
    setRetry(null)
    onChanged()
    qc.invalidateQueries({ queryKey: ['doctor', 'working-rules'] })
    qc.invalidateQueries({ queryKey: ['doctor', 'schedule'] })
  }

  const handleErr = (e: unknown, next: NonNullable<typeof retry>) => {
    const c = getConflict(e)
    if (c) {
      setConflict(c)
      setRetry(next)
      return
    }
    if (e instanceof ApiError) setErrors(e.fields)
    toast.error(e)
  }

  const save = useMutation({
    mutationFn: async ({ resolution }: { resolution?: string }) => {
      if (!edit) return
      const body = {
        weekday: Number(edit.weekday),
        start_time: edit.start_time,
        end_time: edit.end_time,
        slot_minutes: Number(edit.slot_minutes),
        valid_from: edit.valid_from,
        valid_to: edit.valid_to || null,
        clinic_id: edit.clinic_id || null,
      }
      if (edit.id) return api(`/doctor/working-rules/${edit.id}`, { method: 'PATCH', body, query: { resolution } })
      return api('/doctor/working-rules', { method: 'POST', body })
    },
    onSuccess: () => done(edit?.id ? 'Qoida yangilandi' : "Qoida qo'shildi"),
    onError: (e: unknown) => handleErr(e, { kind: 'save' }),
  })

  const remove = useMutation({
    mutationFn: ({ rule, resolution }: { rule: WorkingRule; resolution?: string }) => api(`/doctor/working-rules/${rule.id}`, { method: 'DELETE', query: { resolution } }),
    onSuccess: () => done("Qoida o'chirildi"),
    onError: (e: unknown, v: { rule: WorkingRule; resolution?: string }) => {
      setDel(null)
      handleErr(e, { kind: 'remove', rule: v.rule })
    },
  })

  const byDay = useMemo(() => {
    const m: WorkingRule[][] = Array.from({ length: 7 }, () => [])
    for (const r of q.data ?? []) m[r.weekday]?.push(r)
    m.forEach((l) => l.sort((a, b) => a.start_time.localeCompare(b.start_time)))
    return m
  }, [q.data])

  const clinicName = (id: string | null) => (id ? clinics.find((c) => c.id === id)?.name ?? 'Klinika' : 'Uyga chaqiruv')

  return (
    <>
      <SectionCard
        title="Haftalik ish qoidalari"
        subtitle="Har hafta takrorlanadigan ish vaqtlari. Ulardan slotlar yaratiladi."
        action={
          <Button size="sm" icon={<Plus className="h-4 w-4" />} onClick={() => { setErrors({}); setEdit({ ...emptyRule, clinic_id: clinics[0]?.id ?? '' }) }}>
            Qoida qo'shish
          </Button>
        }
      >
        {q.isLoading && <Skeleton className="h-60" />}
        {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
        {q.data && q.data.length === 0 && (
          <EmptyState icon={<CalendarOff className="h-7 w-7" />} title="Ish qoidasi yo'q" text="Ish vaqtini kiriting — shundan keyin mijozlar sizga yozila oladi." />
        )}
        {q.data && q.data.length > 0 && (
          <div className="divide-y divide-ink-100">
            {byDay.map((rules, wd) => (
              <div key={wd} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center">
                <div className={cx('w-32 shrink-0 font-bold', rules.length ? 'text-ink-900' : 'text-ink-300')}>{WEEKDAYS[wd]}</div>
                <div className="flex flex-1 flex-wrap gap-2">
                  {rules.length === 0 && <span className="text-sm text-ink-400">Dam olish</span>}
                  {rules.map((r) => (
                    <div key={r.id} className="group flex items-center gap-2 rounded-2xl bg-ink-50 py-1.5 pl-3 pr-1.5 ring-1 ring-ink-100">
                      {r.clinic_id ? <MapPin className="h-3.5 w-3.5 text-sky-500" /> : <Home className="h-3.5 w-3.5 text-violet-500" />}
                      <span className="text-sm font-bold tabular-nums text-ink-800">
                        {hhmm(r.start_time)}–{hhmm(r.end_time)}
                      </span>
                      <span className="text-xs text-ink-500">
                        {clinicName(r.clinic_id)} · {r.slot_minutes} daq
                        {r.valid_to && ` · ${dateShort(r.valid_to)} gacha`}
                      </span>
                      <button
                        className="rounded-lg p-1.5 text-ink-400 hover:bg-white hover:text-ink-700"
                        onClick={() => {
                          setErrors({})
                          setEdit({ id: r.id, weekday: r.weekday, start_time: hhmm(r.start_time), end_time: hhmm(r.end_time), slot_minutes: r.slot_minutes, valid_from: r.valid_from, valid_to: r.valid_to ?? '', clinic_id: r.clinic_id ?? '' })
                        }}
                        aria-label="Tahrirlash"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                      <button className="rounded-lg p-1.5 text-ink-400 hover:bg-white hover:text-rose-600" onClick={() => setDel(r)} aria-label="O'chirish">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </SectionCard>

      <Modal
        open={!!edit}
        onClose={() => setEdit(null)}
        title={edit?.id ? 'Qoidani tahrirlash' : 'Yangi ish qoidasi'}
        footer={
          <>
            <Button variant="ghost" onClick={() => setEdit(null)}>
              Bekor
            </Button>
            <Button loading={save.isPending} onClick={() => save.mutate({})}>
              Saqlash
            </Button>
          </>
        }
      >
        {edit && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Hafta kuni" error={errors.weekday} className="sm:col-span-2">
              <div className="grid grid-cols-7 gap-1.5">
                {WEEKDAYS.map((w, i) => (
                  <button
                    key={w}
                    type="button"
                    onClick={() => setEdit({ ...edit, weekday: i })}
                    className={cx('rounded-xl py-2 text-sm font-bold transition', edit.weekday === i ? 'bg-brand-500 text-white shadow-glow' : 'bg-ink-100 text-ink-600 hover:bg-ink-200')}
                  >
                    {w.slice(0, 2)}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="Boshlanishi" error={errors.start_time}>
              <Input type="time" value={edit.start_time} onChange={(e) => setEdit({ ...edit, start_time: e.target.value })} />
            </Field>
            <Field label="Tugashi" error={errors.end_time}>
              <Input type="time" value={edit.end_time} onChange={(e) => setEdit({ ...edit, end_time: e.target.value })} />
            </Field>
            <Field label="Qabul joyi" error={errors.clinic_id}>
              <Select value={edit.clinic_id} onChange={(e) => setEdit({ ...edit, clinic_id: e.target.value })}>
                {clinics.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
                <option value="">🏠 Uyga chaqiruv</option>
              </Select>
            </Field>
            <Field label="Slot davomiyligi" error={errors.slot_minutes}>
              <Select value={edit.slot_minutes} onChange={(e) => setEdit({ ...edit, slot_minutes: Number(e.target.value) })}>
                {SLOT_MINUTES.map((m) => (
                  <option key={m} value={m}>
                    {m} daqiqa
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Amal qilish boshlanishi" error={errors.valid_from}>
              <Input type="date" value={edit.valid_from} onChange={(e) => setEdit({ ...edit, valid_from: e.target.value })} />
            </Field>
            <Field label="Tugash sanasi" hint="Bo'sh — muddatsiz" error={errors.valid_to}>
              <Input type="date" value={edit.valid_to} onChange={(e) => setEdit({ ...edit, valid_to: e.target.value })} />
            </Field>
          </div>
        )}
      </Modal>

      <ConfirmDialog
        open={!!del}
        onClose={() => setDel(null)}
        onConfirm={() => del && remove.mutate({ rule: del })}
        loading={remove.isPending}
        danger
        title="Qoidani o'chirish"
        confirmText="O'chirish"
        text={del ? `${WEEKDAYS[del.weekday]}, ${hhmm(del.start_time)}–${hhmm(del.end_time)} ish vaqti o'chiriladi. Kelajakdagi bo'sh slotlar yopiladi.` : ''}
      />

      <ConflictModal
        conflict={conflict}
        onClose={() => {
          setConflict(null)
          setRetry(null)
        }}
        loading={save.isPending || remove.isPending}
        onResolve={(r) => {
          if (retry?.kind === 'save') save.mutate({ resolution: r })
          else if (retry?.kind === 'remove') remove.mutate({ rule: retry.rule, resolution: r })
        }}
      />
    </>
  )
}

// ---------------------------------------------------------------------------
function TimeOffTab() {
  const qc = useQueryClient()
  const toast = useToast()
  const q = useQuery({ queryKey: ['doctor', 'time-off'], queryFn: () => api<TimeOff[]>('/doctor/time-off') })
  const [form, setForm] = useState<null | { start: string; end: string; kind: string; reason: string }>(null)
  const [del, setDel] = useState<TimeOff | null>(null)
  const [conflict, setConflict] = useState<ConflictState | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})

  const toUtc = (local: string) => fromZonedTime(local, TZ).toISOString()

  const create = useMutation({
    mutationFn: ({ resolution }: { resolution?: string }) =>
      api('/doctor/time-off', {
        method: 'POST',
        query: { resolution },
        body: { start_at: toUtc(form!.start), end_at: toUtc(form!.end), kind: form!.kind, reason: form!.reason },
      }),
    onSuccess: () => {
      toast.success("Dam olish qo'shildi, slotlar yopildi")
      setForm(null)
      setConflict(null)
      qc.invalidateQueries({ queryKey: ['doctor', 'time-off'] })
      qc.invalidateQueries({ queryKey: ['doctor', 'schedule'] })
    },
    onError: (e) => {
      const c = getConflict(e)
      if (c) return setConflict(c)
      if (e instanceof ApiError) setErrors(e.fields)
      toast.error(e)
    },
  })

  const remove = useMutation({
    mutationFn: (t: TimeOff) => api<{ reopened_slots: number }>(`/doctor/time-off/${t.id}`, { method: 'DELETE' }),
    onSuccess: (r) => {
      toast.success(`O'chirildi. ${r?.reopened_slots ?? 0} ta slot qayta ochildi`)
      setDel(null)
      qc.invalidateQueries({ queryKey: ['doctor', 'time-off'] })
      qc.invalidateQueries({ queryKey: ['doctor', 'schedule'] })
    },
    onError: (e) => toast.error(e),
  })

  const list = [...(q.data ?? [])].sort((a, b) => b.start_at.localeCompare(a.start_at))

  return (
    <>
      <SectionCard
        title="Ta'til va dam olish"
        subtitle="Bu vaqt oralig'idagi slotlar avtomatik yopiladi"
        action={
          <Button
            size="sm"
            icon={<Plus className="h-4 w-4" />}
            onClick={() => {
              setErrors({})
              setForm({ start: `${addDaysISO(todayISO(), 1)}T09:00`, end: `${addDaysISO(todayISO(), 1)}T18:00`, kind: 'vacation', reason: '' })
            }}
          >
            Dam olish qo'shish
          </Button>
        }
      >
        {q.isLoading && <Skeleton className="h-32" />}
        {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
        {q.data && list.length === 0 && <EmptyState icon={<Plane className="h-7 w-7" />} title="Rejalashtirilgan dam yo'q" text="Ta'til yoki kasallik kunlarini oldindan kiriting — mijozlar u kunlarga yozilmaydi." />}
        <div className="grid gap-3 md:grid-cols-2">
          {list.map((t) => {
            const past = Date.parse(t.end_at) < Date.now()
            return (
              <div key={t.id} className={cx('flex items-start gap-4 rounded-3xl border border-ink-100 p-4', past && 'opacity-60')}>
                <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-50 to-sky-50 text-violet-600">
                  <Plane className="h-5 w-5" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-ink-900">{TIMEOFF_KIND[t.kind] ?? t.kind}</span>
                    {past && <Badge>O'tgan</Badge>}
                  </div>
                  <div className="text-sm text-ink-600">
                    {dateLong(t.start_at)} {time(t.start_at)} — {dateLong(t.end_at)} {time(t.end_at)}
                  </div>
                  {t.reason && <div className="mt-1 text-sm text-ink-500">{t.reason}</div>}
                </div>
                {!past && (
                  <button className="rounded-xl p-2 text-ink-400 hover:bg-rose-50 hover:text-rose-600" onClick={() => setDel(t)} aria-label="O'chirish">
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
              </div>
            )
          })}
        </div>
      </SectionCard>

      <Modal
        open={!!form}
        onClose={() => setForm(null)}
        title="Dam olish qo'shish"
        footer={
          <>
            <Button variant="ghost" onClick={() => setForm(null)}>
              Bekor
            </Button>
            <Button loading={create.isPending} disabled={!form?.start || !form?.end} onClick={() => create.mutate({})}>
              Saqlash
            </Button>
          </>
        }
      >
        {form && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Turi" className="sm:col-span-2">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {Object.entries(TIMEOFF_KIND).map(([k, l]) => (
                  <button key={k} type="button" onClick={() => setForm({ ...form, kind: k })} className={cx('rounded-xl py-2.5 text-sm font-bold transition', form.kind === k ? 'bg-brand-500 text-white' : 'bg-ink-100 text-ink-600 hover:bg-ink-200')}>
                    {l}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="Boshlanishi" error={errors.start_at}>
              <Input type="datetime-local" value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} />
            </Field>
            <Field label="Tugashi" error={errors.end_at}>
              <Input type="datetime-local" value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} />
            </Field>
            <Field label="Izoh" className="sm:col-span-2">
              <Textarea value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} placeholder="Ixtiyoriy" />
            </Field>
            <p className="text-xs text-ink-500 sm:col-span-2">Vaqtlar Toshkent vaqti bo'yicha.</p>
          </div>
        )}
      </Modal>

      <ConfirmDialog
        open={!!del}
        onClose={() => setDel(null)}
        onConfirm={() => del && remove.mutate(del)}
        loading={remove.isPending}
        danger
        title="Dam olishni o'chirish"
        confirmText="O'chirish"
        text="Bu oraliqdagi yopilgan slotlar qayta ochiladi va mijozlar yozila oladi."
      />

      <ConflictModal conflict={conflict} onClose={() => setConflict(null)} loading={create.isPending} onResolve={(r) => create.mutate({ resolution: r })} />
    </>
  )
}
