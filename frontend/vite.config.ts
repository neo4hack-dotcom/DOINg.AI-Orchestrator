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
  build: {
    rollupOptions: {
      output: {
        // Stable vendor chunks: an app change does not bust them.
        manualChunks: (id) => {
          if (!id.includes('node_modules')) return undefined;
          if (/react-markdown|remark|micromark|mdast|unified|hast|unist|vfile|property-information|decode-named/.test(id)) return 'vendor-markdown';
          if (id.includes('lucide-react')) return 'vendor-icons';
          return 'vendor';
        },
      },
    },
  },
});
