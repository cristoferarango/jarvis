import { CORE_HTTP_URL } from '../config'
import { setPersona } from './persona'

/**
 * Qué motores de voz hay de verdad, decidido una vez al arrancar.
 *
 * Sin nada configurado, la cara usa el reconocimiento y la voz del navegador:
 * funciona para cualquiera. Si el núcleo tiene clave de ElevenLabs, transcribe
 * con Scribe y habla con su voz; si tiene faster-whisper instalado, transcribe
 * en local. En ambos casos la clave y el modelo están en el núcleo: el
 * navegador nunca ve un secreto.
 *
 * La misma llamada trae quién es el asistente (nombre, idioma, palabras de
 * activación), que vive en la configuración del núcleo.
 */

export type Capabilities = {
  /** El núcleo transcribe (ElevenLabs Scribe o faster-whisper local). */
  stt: boolean
  /** El núcleo sintetiza voz (voz clonada local o ElevenLabs). */
  tts: boolean
  ttsEngine: 'clonada' | 'elevenlabs' | 'navegador'
  sttEngine: 'elevenlabs' | 'whisper' | 'browser'
}

let current: Capabilities = { stt: false, tts: false, ttsEngine: 'navegador', sttEngine: 'browser' }
let probed = false

export function caps(): Capabilities {
  return current
}

export function capabilitiesProbed(): boolean {
  return probed
}

/** Nunca lanza: si el núcleo no responde, se queda con el navegador. */
export async function probeCapabilities(): Promise<Capabilities> {
  try {
    const res = await fetch(`${CORE_HTTP_URL}/health`, { signal: AbortSignal.timeout(3000) })
    if (res.ok) {
      const h = (await res.json()) as {
        stt?: boolean
        tts?: boolean
        ttsEngine?: Capabilities['ttsEngine']
        sttEngine?: Capabilities['sttEngine']
        name?: string
        lang?: string
        wake?: string[]
        address?: string
        voiceStyle?: 'original' | 'nativa'
      }
      current = {
        stt: Boolean(h.stt),
        tts: Boolean(h.tts),
        ttsEngine: h.ttsEngine ?? (h.tts ? 'elevenlabs' : 'navegador'),
        sttEngine: h.sttEngine ?? (h.stt ? 'elevenlabs' : 'browser'),
      }
      setPersona({
        name: h.name,
        lang: h.lang,
        wake: h.wake,
        address: h.address,
        voiceStyle: h.voiceStyle,
      })
    }
  } catch {
    // Núcleo caído o lento: voz del navegador.
  }
  probed = true
  return current
}

/** Nombre del motor que pone la voz. */
export function ttsLabel(): string {
  const engine = current.ttsEngine
  return engine === 'clonada' ? 'voz clonada' : engine === 'elevenlabs' ? 'ElevenLabs' : 'navegador'
}

/** Etiqueta corta para el HUD: qué pila de voz está en juego. */
export function engineLabel(): string {
  const c = current
  const ears = c.sttEngine === 'whisper' ? 'Whisper local' : c.stt ? 'ElevenLabs' : 'navegador'
  const mouth = ttsLabel()
  return ears === mouth ? ears : `${ears} / voz ${mouth}`
}
