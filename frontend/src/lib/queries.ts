import { useQuery } from '@tanstack/react-query'
import { api, asList } from './api'
import { useAuth } from './auth'
import type { Address, Clinic, DoctorDetail, DoctorList, Patient, Service, Specialization } from './types'

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

export function useDoctor(id?: string) {
  return useQuery({
    queryKey: ['doctor', id],
    queryFn: () => api<DoctorDetail>(`/catalog/doctors/${id}`, { auth: false }),
    enabled: !!id,
  })
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
    queryFn: async () => asList<Patient>(await api('/patient/patients/')),
    enabled: isAuthed,
  })
}

export function useAddresses() {
  const { isAuthed } = useAuth()
  return useQuery({
    queryKey: ['addresses'],
    queryFn: async () => asList<Address>(await api('/patient/addresses/')),
    enabled: isAuthed,
  })
}

export function useFavorites() {
  const { isAuthed, user } = useAuth()
  return useQuery({
    queryKey: ['favorites'],
    queryFn: async () => asList<DoctorList>(await api('/catalog/favorites')),
    enabled: isAuthed && user?.active_role === 'client',
  })
}
