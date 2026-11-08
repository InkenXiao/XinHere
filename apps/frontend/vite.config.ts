import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, 'index.html'),
        recorder: path.resolve(__dirname, 'recorder.html'),
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
      // 插件前端（仓库 plugins/*/frontend）引用平台外壳的接缝别名
      '@app': path.resolve(__dirname, 'src'),
      // 平台外壳反向复用插件命名视图（如场景弹窗）的接缝别名
      '@plugins': path.resolve(__dirname, '../../plugins'),
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
