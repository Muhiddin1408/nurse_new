import { useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  BadgeCheck,
  Ban,
  CheckCircle2,
  ExternalLink,
  FileText,
  GraduationCap,
  Home,
  Languages,
  Phone,
  RefreshCw,
  RotateCcw,
  RotateCw,
  ShieldCheck,
  XCircle,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { api } from '@/lib/api'
import { dateLong, phone, relativeDateTime } from '@/lib/format'
import { useSpecializations } from '@/lib/queries'
import { useToast } from '@/lib/toast'
import { Avatar, Badge, Button, EmptyState, Modal, ErrorState, Field, Skeleton, StatusBadge, Textarea, cx } from '@/components/ui'
import { SlaBadge } from './DoctorsQueue'
import { DOC_KIND, DOCTOR_STATUS, LANG, type ModDocument, type ModerationDoctor } from './types'

type Action = 'approve' | 'reject' | 'suspend' | 'reinstate'
const ACTIONS: Record<Action, { title: string; text: string; confirm: string; danger?: boolean; reason?: boolean; done: string }> = {
  approve: { title: 'Shifokorni tasdiqlash', text: "Shifokor katalogda ko'rinadi va bron qabul qila boshlaydi.", confirm: 'Tasdiqlash', done: 'Shifokor tasdiqlandi' },
  reject: { title: 'Arizani rad etish', text: "Sabab shifokorga ko'rsatiladi — u tuzatib qayta yuborishi mumkin.", confirm: 'Rad etish', danger: true, reason: true, done: 'Ariza rad etildi' },
  suspend: { title: "Faoliyatni to'xtatish", text: "Shifokor katalogdan yashiriladi va yangi bron qabul qilmaydi.", confirm: "To'xtatish", danger: true, reason: true, done: "Faoliyat to'xtatildi" },
  reinstate: { title: 'Faoliyatni tiklash', text: 'Shifokor yana katalogda ko‘rinadi.', confirm: 'Tiklash', done: 'Faoliyat tiklandi' },
}

export default function DoctorReview() {
  const { id } = useParams()
  const qc = useQueryClient()
  const toast = useToast()
  const specs = useSpecializations()
  const [action, setAction] = useState<Action | null>(null)
  const [reason, setReason] = useState('')
  const [docIdx, setDocIdx] = useState(0)

  const q = useQuery({ queryKey: ['mod', 'doctor', id], queryFn: () => api<ModerationDoctor>(`/moderation/doctors/${id}`), enabled: !!id })

  const act = useMutation({
    mutationFn: (a: Action) => api<ModerationDoctor>(`/moderation/doctors/${id}/${a}`, { method: 'POST', body: ACTIONS[a].reason ? { reason: reason.trim() } : undefined }),
    onSuccess: (d, a) => {
      qc.setQueryData(['mod', 'doctor', id], d)
      qc.invalidateQueries({ queryKey: ['mod', 'doctors'] })
      toast.success(ACTIONS[a].done)
      setAction(null)
      setReason('')
    },
    onError: (e) => toast.error(e),
  })

  const back = (
    <Link to="/admin" className="mb-4 inline-flex items-center gap-1.5 text-sm font-semibold text-ink-500 hover:text-ink-800">
      <ArrowLeft className="h-4 w-4" /> Navbatga qaytish
    </Link>
  )

  if (q.isLoading)
    return (
      <div>
        {back}
        <div className="grid gap-6 xl:grid-cols-2">
          <Skeleton className="h-[480px] rounded-3xl" />
          <Skeleton className="h-[480px] rounded-3xl" />
        </div>
      </div>
    )
  if (q.error || !q.data)
    return (
      <div>
        {back}
        <div className="card"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
      </div>
    )

  const d = q.data
  const docs = d.documents ?? []
  const specNames = d.specialization_ids.map((sid) => specs.data?.find((s) => s.id === sid)?.name ?? '…')
  const licenseExpired = d.license_expires_at && d.license_expires_at < new Date().toISOString().slice(0, 10)

  return (
    <div>
      {back}
      <div className="card mb-6 flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-4">
          <Avatar name={d.full_name} size={60} />
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-extrabold text-ink-900 sm:text-2xl">{d.full_name || 'Ism kiritilmagan'}</h1>
              <StatusBadge status={d.status} map={DOCTOR_STATUS} />
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-ink-500">
              <a href={`tel:${d.phone}`} className="flex items-center gap-1.5 hover:text-brand-700"><Phone className="h-4 w-4" />{phone(d.phone)}</a>
              {d.status === 'pending' && <SlaBadge due={d.sla_due_at} />}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {(d.status === 'pending' || d.status === 'rejected') && (
            <Button icon={<CheckCircle2 className="h-4 w-4" />} onClick={() => setAction('approve')}>Tasdiqlash</Button>
          )}
          {d.status === 'pending' && (
            <Button variant="outline" className="text-rose-600" icon={<XCircle className="h-4 w-4" />} onClick={() => setAction('reject')}>Rad etish</Button>
          )}
          {d.status === 'approved' && (
            <Button variant="outline" className="text-rose-600" icon={<Ban className="h-4 w-4" />} onClick={() => setAction('suspend')}>To'xtatish</Button>
          )}
          {d.status === 'suspended' && (
            <Button icon={<ShieldCheck className="h-4 w-4" />} onClick={() => setAction('reinstate')}>Tiklash</Button>
          )}
        </div>
      </div>

      {d.status_reason && (d.status === 'rejected' || d.status === 'suspended') && (
        <div className="mb-6 rounded-3xl bg-rose-50 p-4 text-sm text-rose-800 ring-1 ring-rose-200">
          <b>Sabab:</b> {d.status_reason}
          {d.moderated_at && <span className="ml-2 text-rose-600/70">· {relativeDateTime(d.moderated_at)}</span>}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-2">
        {/* Chap: anketa */}
        <div className="card space-y-5 p-5 sm:p-6">
          <h2 className="text-lg font-bold text-ink-900">Anketa</h2>
          <div className="flex flex-wrap gap-1.5">
            {specNames.length ? specNames.map((s) => <Badge key={s} tone="green">{s}</Badge>) : <span className="text-sm text-ink-400">Mutaxassislik ko'rsatilmagan</span>}
          </div>
          <dl className="grid gap-4 sm:grid-cols-2">
            <Info label="Tajriba" value={`${d.experience_years} yil`} />
            <Info
              label="Litsenziya"
              value={
                <span>
                  {d.license_number || '—'}
                  {d.license_expires_at && (
                    <span className={cx('block text-xs', licenseExpired ? 'font-bold text-rose-600' : 'text-ink-400')}>
                      {licenseExpired ? 'Muddati o‘tgan: ' : 'Amal qiladi: '}
                      {dateLong(d.license_expires_at)}
                    </span>
                  )}
                </span>
              }
            />
            <Info label={<span className="flex items-center gap-1.5"><Languages className="h-3.5 w-3.5" />Tillar</span>} value={d.languages.map((l) => LANG[l] ?? l).join(', ') || '—'} />
            <Info
              label={<span className="flex items-center gap-1.5"><Home className="h-3.5 w-3.5" />Uyga chaqiruv</span>}
              value={d.accepts_home_visits ? `Ha, ${d.home_visit_radius_km} km radiusda` : "Yo'q"}
            />
            <Info label="Yuborilgan" value={d.submitted_at ? relativeDateTime(d.submitted_at) : '—'} />
            <Info label="Moderatsiya" value={d.moderated_at ? relativeDateTime(d.moderated_at) : '—'} />
          </dl>
          <div>
            <div className="mb-1.5 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-ink-400"><GraduationCap className="h-3.5 w-3.5" />Ta'lim</div>
            <p className="whitespace-pre-line text-[15px] text-ink-700">{d.education || '—'}</p>
          </div>
          <div>
            <div className="mb-1.5 text-xs font-bold uppercase tracking-wider text-ink-400">Bio</div>
            <p className="whitespace-pre-line text-[15px] leading-relaxed text-ink-700">{d.bio || '—'}</p>
          </div>
        </div>

        {/* O'ng: hujjatlar */}
        <div className="card flex flex-col overflow-hidden xl:sticky xl:top-24 xl:max-h-[calc(100vh-7rem)]">
          <div className="flex items-center justify-between gap-3 border-b border-ink-100 px-5 py-3.5">
            <h2 className="flex items-center gap-2 text-lg font-bold text-ink-900"><FileText className="h-5 w-5 text-brand-600" />Hujjatlar ({docs.length})</h2>
            <Button variant="ghost" size="sm" icon={<RefreshCw className={cx('h-4 w-4', q.isFetching && 'animate-spin')} />} onClick={() => q.refetch()} title="Havolalar 5 daqiqa amal qiladi">
              Yangilash
            </Button>
          </div>
          {docs.length === 0 ? (
            <EmptyState icon={<FileText className="h-7 w-7" />} title="Hujjat yuklanmagan" text="Shifokor diplom va litsenziya yuklamagan." />
          ) : (
            <>
              <div className="flex gap-2 overflow-x-auto border-b border-ink-100 px-4 py-3 scrollbar-none">
                {docs.map((doc, i) => (
                  <button
                    key={doc.id}
                    onClick={() => setDocIdx(i)}
                    className={cx('shrink-0 rounded-xl px-3 py-2 text-left text-xs font-semibold ring-1 transition', i === docIdx ? 'bg-brand-50 text-brand-800 ring-brand-200' : 'bg-white text-ink-600 ring-ink-200 hover:bg-ink-50')}
                  >
                    <div>{DOC_KIND[doc.kind] ?? doc.kind}</div>
                    <div className="max-w-[140px] truncate font-normal text-ink-400">{doc.original_name}</div>
                  </button>
                ))}
              </div>
              <DocViewer key={docs[docIdx]?.id} doc={docs[Math.min(docIdx, docs.length - 1)]} />
            </>
          )}
        </div>
      </div>

      {action && (
        <Modal
          open
          onClose={() => setAction(null)}
          title={ACTIONS[action].title}
          size="sm"
          footer={
            <>
              <Button variant="ghost" onClick={() => setAction(null)}>Bekor</Button>
              <Button
                variant={ACTIONS[action].danger ? 'danger' : 'primary'}
                loading={act.isPending}
                disabled={!!ACTIONS[action].reason && !reason.trim()}
                onClick={() => act.mutate(action)}
              >
                {ACTIONS[action].confirm}
              </Button>
            </>
          }
        >
          <p className="text-[15px] leading-relaxed text-ink-600">{ACTIONS[action].text}</p>
          {ACTIONS[action].reason && (
            <Field label="Sabab (majburiy)" className="mt-4">
              <Textarea autoFocus rows={4} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Masalan: litsenziya nusxasi o'qilmaydi, iltimos aniqroq rasm yuklang" />
            </Field>
          )}
        </Modal>
      )}
    </div>
  )
}

function Info({ label, value }: { label: ReactNode; value: ReactNode }) {
  return (
    <div className="rounded-2xl bg-ink-50/70 p-3.5">
      <dt className="text-xs font-bold uppercase tracking-wider text-ink-400">{label}</dt>
      <dd className="mt-1 font-semibold text-ink-800">{value}</dd>
    </div>
  )
}

function DocViewer({ doc }: { doc: ModDocument }) {
  const [zoom, setZoom] = useState(1)
  const [rot, setRot] = useState(0)
  const isImage = doc.content_type.startsWith('image/')
  const isPdf = doc.content_type === 'application/pdf'

  return (
    <div className="flex min-h-[420px] flex-1 flex-col">
      <div className="flex items-center justify-between gap-2 px-4 py-2.5 text-xs text-ink-500">
        <span className="truncate">{doc.original_name} · {(doc.size / 1024).toFixed(0)} KB</span>
        <div className="flex items-center gap-1">
          {isImage && (
            <>
              <IconBtn label="Kichraytirish" onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))}><ZoomOut className="h-4 w-4" /></IconBtn>
              <span className="w-10 text-center font-semibold tabular-nums">{Math.round(zoom * 100)}%</span>
              <IconBtn label="Kattalashtirish" onClick={() => setZoom((z) => Math.min(4, z + 0.25))}><ZoomIn className="h-4 w-4" /></IconBtn>
              <IconBtn label="Chapga burish" onClick={() => setRot((r) => r - 90)}><RotateCcw className="h-4 w-4" /></IconBtn>
              <IconBtn label="O'ngga burish" onClick={() => setRot((r) => r + 90)}><RotateCw className="h-4 w-4" /></IconBtn>
            </>
          )}
          <a href={doc.download_url} target="_blank" rel="noreferrer" className="ml-1 flex items-center gap-1 rounded-lg px-2 py-1.5 font-semibold text-brand-700 hover:bg-brand-50">
            <ExternalLink className="h-4 w-4" /> Ochish
          </a>
        </div>
      </div>
      <div className="relative flex-1 overflow-auto bg-ink-100/70">
        {isImage ? (
          <div className="flex min-h-full items-center justify-center p-4">
            <img
              src={doc.download_url}
              alt={doc.original_name}
              className="max-w-full rounded-lg shadow-soft transition-transform duration-200"
              style={{ transform: `scale(${zoom}) rotate(${rot}deg)`, transformOrigin: 'center' }}
            />
          </div>
        ) : isPdf ? (
          <iframe src={doc.download_url} title={doc.original_name} className="h-full min-h-[420px] w-full border-0" />
        ) : (
          <div className="flex h-full min-h-[300px] flex-col items-center justify-center gap-3 p-6 text-center text-sm text-ink-500">
            <BadgeCheck className="h-8 w-8 text-ink-300" />
            Bu formatni brauzerda ko'rsatib bo'lmaydi.
            <a href={doc.download_url} target="_blank" rel="noreferrer"><Button size="sm" variant="outline">Yuklab olish</Button></a>
          </div>
        )}
      </div>
    </div>
  )
}

function IconBtn({ label, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" aria-label={label} title={label} onClick={onClick} className="rounded-lg p-1.5 text-ink-500 transition hover:bg-ink-100 hover:text-ink-800">
      {children}
    </button>
  )
}
