import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/** Donde escucha el núcleo Python (`crisvis`). */
const CORE = process.env.CRISVIS_CORE ?? 'http://127.0.0.1:8787'

/** Todo lo que sirve el núcleo. En desarrollo Vite lo reenvía, así la cara usa
 *  siempre URLs relativas al mismo origen, igual que en producción. */
const CORE_ROUTES = ['/health', '/tts', '/stt', '/img', '/media', '/page', '/file', '/api']

/**
 * CSP solo de desarrollo. Igual que la de producción (src/crisvis/security/
 * headers.py) salvo 'unsafe-inline' en scripts, que necesita el preámbulo de
 * React Fast Refresh, y el WebSocket de recarga en caliente de Vite. Nunca llega
 * al build: la página compilada la sirve el núcleo con su propia cabecera.
 */
const DEV_CSP = [
  "default-src 'self'",
  "base-uri 'self'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "form-action 'self'",
  "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' data: https://fonts.gstatic.com",
  "img-src 'self' data: blob:",
  "media-src 'self' data: blob:",
  "connect-src 'self' data: blob: ws://localhost:* ws://127.0.0.1:* https://storage.googleapis.com",
  "worker-src 'self' blob:",
  "frame-src 'self' https://www.youtube-nocookie.com https://www.youtube.com https://player.vimeo.com",
].join('; ')

export default defineConfig({
  plugins: [react()],
  server: {
    // El núcleo acepta orígenes localhost:5173-5199 en desarrollo.
    port: Number(process.env.PORT) || 5173,
    strictPort: false,
    headers: { 'Content-Security-Policy': DEV_CSP },
    proxy: {
      '/ws': { target: CORE, ws: true, changeOrigin: false },
      ...Object.fromEntries(CORE_ROUTES.map((r) => [r, { target: CORE, changeOrigin: false }])),
    },
  },
  build: {
    // El núcleo sirve la cara compilada desde su propio paquete: una sola
    // aplicación, un solo proceso, un solo puerto.
    outDir: fileURLToPath(new URL('../../src/crisvis/static', import.meta.url)),
    emptyOutDir: true,
    chunkSizeWarningLimit: 4096,
  },
  optimizeDeps: {
    // kokoro-js pulls in `phonemizer`, which carries espeak-ng as inline WASM.
    // Vite's dependency pre-bundler rewrites that initialisation and the
    // language table ends up empty — the symptom is
    // `Invalid language identifier: "en". Should be one of: .` at generate()
    // time, long after the model has loaded successfully. Serving these
    // untouched fixes it.
    exclude: ['kokoro-js', 'phonemizer', '@huggingface/transformers'],
  },
})
