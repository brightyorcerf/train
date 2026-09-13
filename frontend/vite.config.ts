import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Node's env, without pulling in @types/node just for one lookup.
declare const process: { env: Record<string, string | undefined> }

// The backend has no CORS middleware, so the dev server proxies instead of the API
// loosening its origin policy. API_URL is set to http://api:8000 by compose; on the
// host it defaults to the published port.
const target = process.env.API_URL || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: { '/api': { target, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, '') } },
  },
})
