/**
 * Configuración de la cara.
 *
 * La cara no tiene backend propio: habla siempre con el núcleo de Crisvis, que
 * la sirve desde su mismo origen (o, en desarrollo, a través del proxy de
 * Vite). Por eso las URLs del núcleo son relativas y ninguna clave viaja en el
 * bundle: los secretos viven en ~/.crisvis/crisvis.toml, del lado del núcleo.
 *
 * Lo que sí se puede ajustar aquí son preferencias del navegador, con
 * variables VITE_* en apps/face/.env.local (ver .env.example).
 */

/**
 * Vite convierte una entrada vacía del .env en '', no en undefined, así que `??`
 * nunca cae al valor por defecto. Aquí todo lo vacío cuenta como no definido.
 */
function str(raw: unknown): string | undefined {
  const value = typeof raw === 'string' ? raw.trim() : ''
  return value === '' ? undefined : value
}

/** Opciones cerradas. Un valor desconocido casi siempre es una errata. */
function choice<T extends string>(
  name: string,
  raw: unknown,
  allowed: readonly T[],
  fallback: T,
): T {
  const value = str(raw)
  if (value === undefined) return fallback
  if ((allowed as readonly string[]).includes(value)) return value as T
  console.warn(`[crisvis] ${name}="${value}" no es ${allowed.join(' | ')}; uso "${fallback}".`)
  return fallback
}

/** Ruta HTTP del núcleo. Vacía = mismo origen. */
export const CORE_HTTP_URL = (str(import.meta.env.VITE_CORE_URL) ?? '').replace(/\/$/, '')

/** El WebSocket del núcleo, derivado de la página si no se indica otro. */
export const CORE_WS_URL = CORE_HTTP_URL
  ? `${CORE_HTTP_URL.replace(/^http/, 'ws')}/ws`
  : `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`

/**
 * Motor de voz de salida.
 *
 *   'system' — la síntesis del navegador. Empieza al instante y no cuesta nada;
 *     en Windows con Edge/Chrome hay voces españolas neuronales muy dignas.
 *   'kokoro' — TTS neuronal en el navegador (ONNX). Solo trae voces inglesas,
 *     así que solo tiene sentido con el asistente en inglés.
 *
 * Si el núcleo tiene clave de ElevenLabs, se usa ElevenLabs por encima de ambos.
 */
export const TTS_ENGINE: 'kokoro' | 'system' = choice(
  'VITE_TTS_ENGINE',
  import.meta.env.VITE_TTS_ENGINE,
  ['kokoro', 'system'] as const,
  'system',
)

/** Voz de Kokoro (todas en inglés británico). */
export const KOKORO_VOICE = choice(
  'VITE_KOKORO_VOICE',
  import.meta.env.VITE_KOKORO_VOICE,
  ['bm_george', 'bm_fable', 'bm_lewis', 'bm_daniel'] as const,
  'bm_george',
)

export const env = {
  /** Clave pública de Picovoice para la palabra de activación offline. */
  porcupineKey: str(import.meta.env.VITE_PICOVOICE_ACCESS_KEY) ?? '',
}

/**
 * Motor de palabra de activación.
 *   'speech'    — el SpeechRecognition del navegador. Sin configurar nada;
 *                 Chrome/Edge envían el audio a su servicio de reconocimiento.
 *   'porcupine' — offline en WASM, con "Jarvis" como palabra integrada.
 *                 Necesita una AccessKey gratuita de console.picovoice.ai.
 */
export const WAKE_ENGINE: 'speech' | 'porcupine' = env.porcupineKey ? 'porcupine' : 'speech'
