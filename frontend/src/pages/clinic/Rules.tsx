import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, DoorOpen, ListChecks } from 'lucide-react'
import { relativeDateTime } from '@/lib/format'
import { useToast } from '@/lib/toast'
import { Avatar, EmptyState, ErrorState, ListSkeleton, Modal, PageHeader, Button, cx } from '@/components/ui'
import { useClinic } from './ctx'
import { WEEKDAY_NAMES, hhmm } from './shared'
import type { ClinicRule, Room } from './types'

interface AssignResult { moved: number; blocked: number; conflicts: string[] }

function toMin(t: string) {
  const [h, m] = t.split(':').map(Number)
  return h * 60 + m
}

export default function Rules() {
  const { capi, clinicId } = useClinic()
  const qc = useQueryClient()
  const toast = useToast()
  const [result, setResult] = useState<{ rule: ClinicRule; res: AssignResult } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const rules = useQuery({ queryKey: ['clinic', clinicId, 'rules'], queryFn: () => capi<ClinicRule[]>('/clinic/rules') })
  const rooms = useQuery({ queryKey: ['clinic', clinicId, 'rooms'], queryFn: () => capi<Room[]>('/clinic/rooms') })

  const assign = useMutation({
    mutationFn: ({ rule, roomId }: { rule: ClinicRule; roomId: string | null }) =>
      capi<AssignResult>(`/clinic/rules/${rule.rule_id}/room`, { method: 'POST', body: { room_id: roomId } }),
    onMutate: ({ rule }) => setBusy(rule.rule_id),
    onSettled: () => setBusy(null),
    onSuccess: (res, { rule }) => {
      if (res.conflicts.length || res.blocked) setResult({ rule, res })
      else toast.success(`Xona biriktirildi · ${res.moved} ta slot ko'chirildi`)
      qc.invalidateQueries({ queryKey: ['clinic', clinicId, 'rules'] })
      qc.invalidateQueries({ queryKey: ['clinic', clinicId, 'schedule'] })
    },
    onError: (e) => toast.error(e),
  })

  // Bir xonaga bir vaqtda ikki qoida — darhol qizil
  const clashes = useMemo(() => {
    const set = new Set<string>()
    const list = rules.data ?? []
    for (let i = 0; i < list.length; i++)
      for (let j = i + 1; j < list.length; j++) {
        const a = list[i]
        const b = list[j]
        if (!a.room_id || a.room_id !== b.room_id || a.weekday !== b.weekday) continue
        if (toMin(a.start_time) < toMin(b.end_time) && toMin(b.start_time) < toMin(a.end_time)) {
          set.add(a.rule_id)
          set.add(b.rule_id)
        }
      }
    return set
  }, [rules.data])

  const byDay = useMemo(() => {
    const m = new Map<number, ClinicRule[]>()
    for (const r of rules.data ?? []) m.set(r.weekday, [...(m.get(r.weekday) ?? []), r])
    for (const v of m.values()) v.sort((a, b) => a.start_time.localeCompare(b.start_time))
    return [...m.entries()].sort((a, b) => a[0] - b[0])
  }, [rules.data])

  const activeRooms = (rooms.data ?? []).filter((r) => r.is_active)

  return (
    <div>
      <PageHeader title="Ish qoidalari" subtitle="Shifokorlarning haftalik jadvali va xonalarga biriktirish" />
      {clashes.size > 0 && (
        <div className="mb-5 flex items-start gap-3 rounded-3xl bg-rose-50 p-4 text-sm text-rose-800 ring-1 ring-rose-200">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
          <div><b>Xona to'qnashuvi:</b> bir xonaga bir vaqtda bir nechta qoida biriktirilgan. Qizil belgilangan qatorlarni tekshiring.</div>
        </div>
      )}
      {rules.isLoading ? (
        <ListSkeleton rows={5} />
      ) : rules.error ? (
        <div className="card"><ErrorState error={rules.error} onRetry={() => rules.refetch()} /></div>
      ) : byDay.length === 0 ? (
        <div className="card"><EmptyState icon={<ListChecks className="h-7 w-7" />} title="Ish qoidalari yo'q" text="Shifokorlar o'z kabinetida klinika uchun ish vaqtini kiritganda shu yerda ko'rinadi." /></div>
      ) : (
        <div className="space-y-5">
          {byDay.map(([wd, list]) => (
            <div key={wd} className="card overflow-hidden">
              <div className="flex items-center justify-between border-b border-ink-100 bg-ink-50/60 px-5 py-3">
                <h3 className="font-bold text-ink-900">{WEEKDAY_NAMES[wd]}</h3>
                <span className="text-xs font-semibold text-ink-500">{list.length} ta qoida</span>
              </div>
              <ul className="divide-y divide-ink-100">
                {list.map((r) => (
                  <li key={r.rule_id} className={cx('flex flex-col gap-3 px-5 py-3.5 sm:flex-row sm:items-center', clashes.has(r.rule_id) && 'bg-rose-50/70')}>
                    <div className="flex min-w-0 flex-1 items-center gap-3">
                      <Avatar name={r.doctor} size={38} className="rounded-xl" />
                      <div className="min-w-0">
                        <div className="truncate font-semibold text-ink-900">{r.doctor}</div>
                        <div className="text-sm tabular-nums text-ink-500">{hhmm(r.start_time)} – {hhmm(r.end_time)}</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 sm:w-64">
                      <DoorOpen className={cx('h-4 w-4 shrink-0', clashes.has(r.rule_id) ? 'text-rose-500' : 'text-ink-400')} />
                      <select
                        value={r.room_id ?? ''}
                        disabled={busy === r.rule_id}
                        onChange={(e) => assign.mutate({ rule: r, roomId: e.target.value || null })}
                        className={cx('field !rounded-xl !py-2 text-sm', clashes.has(r.rule_id) && '!border-rose-300 !text-rose-700')}
                      >
                        <option value="">Xona biriktirilmagan</option>
                        {activeRooms.map((room) => (
                          <option key={room.id} value={room.id}>{room.name}{room.floor ? ` · ${room.floor}-qavat` : ''}</option>
                        ))}
                        {r.room_id && !activeRooms.some((x) => x.id === r.room_id) && <option value={r.room_id}>{r.room}</option>}
                      </select>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {result && (
        <Modal open onClose={() => setResult(null)} title="Xona biriktirildi" size="sm" footer={<Button onClick={() => setResult(null)}>Tushunarli</Button>}>
          <div className="space-y-3 text-sm">
            <div className="flex items-center gap-2 text-ink-700"><CheckCircle2 className="h-5 w-5 text-brand-600" /> {result.res.moved} ta slot yangi xonaga ko'chirildi</div>
            {result.res.blocked > 0 && (
              <div className="flex items-center gap-2 text-amber-700"><AlertTriangle className="h-5 w-5" /> {result.res.blocked} ta bo'sh slot yopildi (xona band)</div>
            )}
            {result.res.conflicts.length > 0 && (
              <div className="rounded-2xl bg-rose-50 p-3 ring-1 ring-rose-200">
                <div className="mb-2 font-bold text-rose-800">To'qnashuvlar ({result.res.conflicts.length})</div>
                <ul className="space-y-1 text-rose-700">
                  {result.res.conflicts.map((c) => <li key={c}>• {relativeDateTime(c)}</li>)}
                </ul>
                <p className="mt-2 text-xs text-rose-600">Bu vaqtlarda xona boshqa qabul bilan band — qabullarni qo'lda ko'chiring.</p>
              </div>
            )}
          </div>
        </Modal>
      )}
    </div>
  )
}
