import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
      '@contracts': path.resolve(__dirname, '../../packages/contracts/generated/ts'),
    },
  },
  server: {
    port: 5173,
    fs: { allow: [path.resolve(__dirname, '../..')] },
    proxy: {
      '/api': { target: 'http://localhost:8100', changeOrigin: true },
    },
  },
})
