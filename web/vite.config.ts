import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

const BACKEND = 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    // The app calls relative /api and /media, so the built files work unchanged when the backend serves them.
    proxy: { '/api': BACKEND, '/media': BACKEND },
  },
  test: { environment: 'node' },
});
