import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import path from 'node:path'

// Brauzer so'rovlari Vite proxy orqali bir xil origin'dan Kong'ga (API gateway) ketadi:
// /v1/* — mikroservislar, /ws — real-time (ws-gateway).
const BACKEND = process.env.VITE_BACKEND_URL || 'http://127.0.0.1:8000'

const proxy = {
  '/v1': { target: BACKEND, changeOrigin: true },
  '/ws': { target: BACKEND.replace(/^http/, 'ws'), ws: true, changeOrigin: true },
}

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(import.meta.dirname, 'src') } },
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
})
