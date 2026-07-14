import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Puerto del backend FastAPI. Por defecto 8010; overridable con la variable de
// entorno VITE_BACKEND (ej: VITE_BACKEND=http://127.0.0.1:9999 npm run dev).
// Debe coincidir con el --port que le pases a uvicorn.
const BACKEND = process.env.VITE_BACKEND || 'http://127.0.0.1:8010'

// El build sale a frontend/dist, que FastAPI sirve en "/".
// base relativo para que los assets carguen servidos desde el mismo origen.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      '/api': BACKEND,
      '/media': BACKEND,
      '/reportes': BACKEND,
    },
  },
})
