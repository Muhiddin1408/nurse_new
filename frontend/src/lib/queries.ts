import { useQueries, useQuery } from '@tanstack/react-query'
import { api, asList } from './api'
import { useAuth } from './auth'
import type { Address, Clinic, DoctorCard, DoctorDetail, DoctorList, Patient, SearchResults, Service, Specialization } from './types'

export function useSpecializations() {
  return useQuery({
    queryKey: ['specializations'],
    queryFn: () => api<Specialization[]>('/catalog/specializations', { auth: false }),
    staleTime: 10 * 60_000,
  })
}

export function useClinics() {
  return useQuery({
    queryKey: ['clinics'],
    queryFn: async () => asList<Clinic>(await api('/catalog/clinics', { auth: false, query: { limit: 100 } })),
    staleTime: 5 * 60_000,
  })
}

/** clinic_id → nom (bron va qabullarda klinika nomi catalog'dan; booking faqat ID saqlaydi) */
export function useClinicNames() {
  const q = useClinics()
  return (id?: string | null) => (id ? q.data?.find((c) => c.id === id) : undefined)
}

export function useDoctor(id?: string) {
  return useQuery({
    queryKey: ['doctor', id],
    queryFn: () => api<DoctorDetail>(`/catalog/doctors/${id}`, { auth: false }),
    enabled: !!id,
    staleTime: 5 * 60_000,
  })
}

/** Bir nechta shifokor (bronlar, sevimlilar, waitlist ro'yxati) — har biri keshda, takror so'ralmaydi. */
export function useDoctors(ids: (string | null | undefined)[]) {
  const unique = Array.from(new Set(ids.filter((x): x is string => !!x)))
  const results = useQueries({
    queries: unique.map((id) => ({
      queryKey: ['doctor', id],
      queryFn: () => api<DoctorDetail>(`/catalog/doctors/${id}`, { auth: false }),
      staleTime: 5 * 60_000,
      retry: false,
    })),
  })
  const byId = new Map<string, DoctorDetail>()
  results.forEach((r, i) => r.data && byId.set(unique[i], r.data))
  return { byId, isLoading: results.some((r) => r.isLoading) }
}

export function useDoctorServices(id?: string, place?: string) {
  return useQuery({
    queryKey: ['doctor-services', id, place ?? 'all'],
    queryFn: () => api<Service[]>(`/catalog/doctors/${id}/services`, { auth: false, query: { place } }),
    enabled: !!id,
  })
}

export function usePatients() {
  const { isAuthed } = useAuth()
  return useQuery({
    queryKey: ['patients'],
    queryFn: () => api<Patient[]>('/users/me/patients'),
    enabled: isAuthed,
  })
}

export function useAddresses() {
  const { isAuthed } = useAuth()
  return useQuery({
    queryKey: ['addresses'],
    queryFn: () => api<Address[]>('/users/me/addresses'),
    enabled: isAuthed,
  })
}

/** Sevimli shifokorlar ID'lari (user-service). Tafsilot — `useDoctors(ids)`. */
export function useFavorites() {
  const { isAuthed, user } = useAuth()
  return useQuery({
    queryKey: ['favorites'],
    queryFn: async () => (await api<{ doctor_id: string; created_at: string }[]>('/users/me/favorites')).map((f) => f.doctor_id),
    enabled: isAuthed && user?.active_role === 'client',
  })
}

export interface DoctorSearch {
  q?: string
  specialization?: string
  home?: boolean
  sort?: string
  lat?: string | number
  lng?: string | number
  limit?: number
  offset?: number
}

/**
 * Qidiruv (search-service, Elasticsearch) → katalog kartasi (`DoctorList`) ko'rinishida — sahifalar bitta tipda ishlaydi.
 * ES ishlamasa servis Postgres zaxirasidan javob beradi (`degraded`), natija baribir keladi.
 */
export async function searchDoctors(p: DoctorSearch): Promise<{ results: DoctorList[]; total: number | null; degraded: boolean }> {
  const r = await api<SearchResults>('/search/doctors', {
    auth: false,
    query: {
      q: p.q,
      specialization: p.specialization,
      place: p.home ? 'home' : undefined,
      sort: p.sort,
      lat: p.lat,
      lng: p.lng,
      limit: p.limit,
      offset: p.offset,
    },
  })
  return { results: r.items.map(cardToList), total: r.total, degraded: r.degraded }
}

function cardToList(c: DoctorCard): DoctorList {
  return {
    id: c.id,
    full_name: c.full_name,
    experience_years: c.experience_years,
    rating: c.rating == null ? null : String(c.rating),
    reviews_count: c.reviews_count,
    // Reyting faqat ≥ 5 sharhdan (review-service) — reytingi yo'q shifokor "yangi"
    is_new: c.rating == null,
    specializations: c.specialization_ids.map((id, i) => ({ id, name: c.specialization_names[i] ?? '', name_ru: '' })),
    accepts_home_visits: c.accepts_home_visits,
    photo_url: null,
    distance_km: c.distance_km,
  }
}
