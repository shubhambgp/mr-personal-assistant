/// <reference types="vitest/config" />
import { fileURLToPath } from 'node:url'

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Mirrored in tsconfig.json `compilerOptions.paths`.
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    // `node` by default — most tests cover pure functions (the SSE reducer,
    // parsers, date helpers). The one component test opts into jsdom with a
    // `// @vitest-environment jsdom` docblock instead of paying the DOM tax
    // everywhere.
    environment: 'node',
  },
  server: {
    port: 5173,
    // The API is same-origin through this proxy in dev, so the httpOnly session
    // cookie is sent without any CORS/SameSite special-casing.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // SSE must not be buffered by the proxy or streaming looks like a
        // single delayed response.
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
              proxyRes.headers['cache-control'] = 'no-cache, no-transform'
            }
          })
        },
      },
    },
  },
})
