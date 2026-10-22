import { api } from './api'

interface UploadTicket {
  file_id: string
  upload_url: string
  method: string
  headers: Record<string, string>
  expires_in: number
}

/**
 * file-service orqali yuklash (ADR-0010): 1) ruxsat (presigned URL) → 2) fayl brauzerdan TO'G'RIDAN-TO'G'RI S3'ga →
 * 3) `complete` (servis hajm/turini tekshiradi). Qaytaradi: `file_id` — keyin egasi servisga biriktiriladi
 * (masalan, catalog `POST /doctor/onboarding/documents {kind, file_id}`).
 */
export async function uploadFile(file: File, purpose: string): Promise<string> {
  const ticket = await api<UploadTicket>('/files/uploads', {
    method: 'POST',
    body: { purpose, content_type: file.type || 'application/octet-stream', size: file.size, filename: file.name },
  })
  const res = await fetch(ticket.upload_url, { method: ticket.method || 'PUT', headers: ticket.headers, body: file })
  if (!res.ok) throw new Error("Faylni yuklab bo'lmadi. Qayta urinib ko'ring.")
  await api(`/files/${ticket.file_id}/complete`, { method: 'POST' })
  return ticket.file_id
}
