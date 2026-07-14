import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// El build sale a frontend/dist, que FastAPI sirve en "/".
// base relativo para que los assets carguen servidos desde el mismo origen.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/media': 'http://127.0.0.1:8000',
    },
  },
})
