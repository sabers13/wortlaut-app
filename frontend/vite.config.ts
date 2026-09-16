import { defineConfig } from 'vite';

export default defineConfig({
  build: {
    outDir: '../app/frontend',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/vocab': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
});
