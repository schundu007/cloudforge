import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Minimal local declaration so the config typechecks without pulling in
// @types/node just for one env lookup.
declare const process: { env: Record<string, string | undefined> }

const apiTarget = process.env.CLOUDFORGE_API_URL ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': apiTarget,
      '/runs': apiTarget,
    }
  }
})
