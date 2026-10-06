// Vite 配置：/api 与 /uploads 代理到后端 8000
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 15173,
    strictPort: true,
    proxy: {
      '/api': { target: process.env.API_PROXY_TARGET || 'http://127.0.0.1:18090', changeOrigin: true },
      '/uploads': { target: process.env.API_PROXY_TARGET || 'http://127.0.0.1:18090', changeOrigin: true },
    },
  },
});
