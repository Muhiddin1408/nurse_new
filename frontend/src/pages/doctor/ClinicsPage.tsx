import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Building2, Check, MailOpen, MapPin, Star, X } from 'lucide-react'
import { api } from '@/lib/api'
import { useToast } from '@/lib/toast'
import { dateLong } from '@/lib/format'
import { Button, EmptyState, ErrorState, ListSkeleton, PageHeader } from '@/components/ui'
import { SectionCard, useMyClinics } from './shared'

interface Invite {
  affiliation_id: string
  clinic_id: string
  clinic: string
  city: string
  street: string
  position: string
  invited_at: string
}

export function useInvites() {
  return useQuery({ queryKey: ['doctor', 'clinic-invites'], queryFn: () => api<Invite[]>('/doctor/clinic-invites'), refetchInterval: 120_000 })
}

export default function ClinicsPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const invites = useInvites()
  const mine = useMyClinics()

  const respond = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'accept' | 'decline' }) => api<{ status: string }>(`/doctor/clinic-invites/${id}/${action}`, { method: 'POST' }),
    onSuccess: (_d, v) => {
      toast.success(v.action === 'accept' ? 'Taklif qabul qilindi — klinika jamoasiga xush kelibsiz!' : 'Taklif rad etildi')
      qc.invalidateQueries({ queryKey: ['doctor', 'clinic-invites'] })
      qc.invalidateQueries({ queryKey: ['doctor', 'public'] })
    },
    onError: (e) => toast.error(e),
  })

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Klinikalar" subtitle="Siz ishlaydigan klinikalar va yangi takliflar" />

      {(invites.data?.length ?? 0) > 0 && (
        <SectionCard title="Yangi takliflar" subtitle="Klinika sizni jamoasiga taklif qilmoqda" className="mb-6 ring-2 ring-brand-200">
          <div className="space-y-3">
            {invites.data!.map((i) => (
              <div key={i.affiliation_id} className="flex flex-col gap-4 rounded-3xl bg-gradient-to-r from-brand-50 to-sky-50 p-4 sm:flex-row sm:items-center">
                <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-white text-brand-600 shadow-soft">
                  <MailOpen className="h-5 w-5" />
                </div>
                <div className="flex-1">
                  <div className="font-bold text-ink-900">{i.clinic}</div>
                  <div className="text-sm text-ink-600">
                    {i.position && `${i.position} · `}
                    {i.city}, {i.street}
                  </div>
                  <div className="text-xs text-ink-400">{dateLong(i.invited_at)} taklif qilingan</div>
                </div>
                <div className="flex gap-2">
                  <Button size="sm" variant="outline" icon={<X className="h-4 w-4" />} disabled={respond.isPending} onClick={() => respond.mutate({ id: i.affiliation_id, action: 'decline' })}>
                    Rad etish
                  </Button>
                  <Button size="sm" icon={<Check className="h-4 w-4" />} loading={respond.isPending && respond.variables?.id === i.affiliation_id && respond.variables.action === 'accept'} disabled={respond.isPending} onClick={() => respond.mutate({ id: i.affiliation_id, action: 'accept' })}>
                    Qabul qilish
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </SectionCard>
      )}

      <SectionCard title="Mening klinikalarim">
        {(mine.isLoading || invites.isLoading) && <ListSkeleton rows={2} />}
        {mine.isError && <ErrorState error={mine.error} onRetry={() => mine.refetch()} />}
        {mine.data && mine.data.clinics.length === 0 && <EmptyState icon={<Building2 className="h-7 w-7" />} title="Hali klinikaga biriktirilmagansiz" text="Klinika admini sizni telefon raqamingiz orqali taklif qiladi. Taklif shu yerda paydo bo'ladi." />}
        <div className="grid gap-3 sm:grid-cols-2">
          {mine.data?.clinics.map((c) => (
            <div key={c.id} className="flex gap-4 rounded-3xl border border-ink-100 p-4">
              <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-400 to-brand-600 text-white shadow-glow">
                <Building2 className="h-6 w-6" />
              </div>
              <div className="min-w-0">
                <div className="font-bold text-ink-900">{c.name}</div>
                <div className="mt-0.5 flex items-start gap-1 text-sm text-ink-500">
                  <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {c.city}, {c.street}
                </div>
                {Number(c.rating) > 0 && (
                  <div className="mt-1 flex items-center gap-1 text-sm font-semibold text-amber-600">
                    <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-400" /> {Number(c.rating).toFixed(1)} <span className="font-normal text-ink-400">({c.reviews_count})</span>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      </SectionCard>
    </div>
  )
}
