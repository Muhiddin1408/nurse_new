import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import path from 'node:path'

// Backend CORS sarlavhasi bermaydi (conf/settings.py), shuning uchun
// brauzer so'rovlari Vite proxy orqali bir xil origin'dan ketadi.
const BACKEND = process.env.VITE_BACKEND_URL || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  server: {
    port: 5173,
    proxy: { '/api': { target: BACKEND, changeOrigin: true } },
  },
  preview: {
    port: 4173,
    proxy: { '/api': { target: BACKEND, changeOrigin: true } },
  },
})
