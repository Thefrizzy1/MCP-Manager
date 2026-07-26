import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath } from 'node:url'

// The built app is served by FastAPI: hashed assets under /spa/, and index.html
// at /app. In dev, proxy the backend API surfaces to the running UI server.
const backend = 'http://localhost:8766'
const proxy = Object.fromEntries(
  ['/api', '/service', '/env', '/settings', '/health', '/server', '/icons', '/tool'].map((p) => [
    p,
    { target: backend, changeOrigin: true },
  ]),
)

export default defineConfig({
  base: '/spa/',
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: { outDir: '../static/dist', emptyOutDir: true, target: 'es2022' },
  server: { proxy },
})
