import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发模式下将 /api 与 /v1 代理到本地后端,避免跨域
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5188,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://localhost:8300', changeOrigin: true },
      '/v1': { target: 'http://localhost:8300', changeOrigin: true },
    },
  },
})
