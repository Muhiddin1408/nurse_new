import { Link } from 'react-router-dom'
import { Compass } from 'lucide-react'
import { Button } from '@/components/ui'

export default function NotFound() {
  return (
    <div className="container-x flex min-h-[60vh] flex-col items-center justify-center py-16 text-center">
      <div className="flex h-20 w-20 items-center justify-center rounded-3xl bg-brand-50 text-brand-600">
        <Compass className="h-10 w-10" />
      </div>
      <div className="mt-6 text-6xl font-extrabold text-ink-200">404</div>
      <h1 className="mt-2 text-2xl font-extrabold text-ink-900">Sahifa topilmadi</h1>
      <p className="mt-2 max-w-sm text-ink-500">Havola eskirgan yoki noto'g'ri yozilgan bo'lishi mumkin.</p>
      <div className="mt-6 flex gap-2">
        <Link to="/"><Button variant="outline">Bosh sahifa</Button></Link>
        <Link to="/doctors"><Button>Shifokor topish</Button></Link>
      </div>
    </div>
  )
}
