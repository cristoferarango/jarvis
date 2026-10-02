import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/** Donde escucha el núcleo Python (`crisvis`). */
const CORE = process.env.CRISVIS_CORE ?? 'http://127.0.0.1:8787'

/** Todo lo que sirve el núcleo. En desarrollo Vite lo reenvía, así la cara usa
 *  siempre URLs relativas al mismo origen, igual que en producción. */
const CORE_ROUTES = ['/health', '/tts', '/stt', '/img', '/media', '/page', '/file', '/api']

export default defineConfig({
  plugins: [react()],
  server: {
    // El núcleo acepta orígenes localhost:5173-5199 en desarrollo.
    port: Number(process.env.PORT) || 5173,
    strictPort: false,
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
