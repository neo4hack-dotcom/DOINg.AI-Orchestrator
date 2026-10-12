import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev: Vite on :4000, API (python server.py) on :4001. NEXUS.AI keeps 3000/3001.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 4000,
    host: true,
    proxy: { '/api': { target: 'http://localhost:4001', changeOrigin: true } },
  },
  base: './',
});
