/**
 * Reglas de seguridad de la cara, sin DOM ni red (se prueban con node --test).
 *
 * Las decisiones de verdad las toma el núcleo; esto solo evita que la cara
 * ofrezca atajos que el núcleo luego tendría que rechazar.
 */
import type { ActionRisk, CancelFrame, PermissionMode, UserPermissionMode } from '@crisvis/protocol'

/** Un «sí» de viva voz no basta para lo de riesgo alto: un eco o una palabra suelta lo darían. */
export function voiceCanApprove(risk: ActionRisk | null | undefined): boolean {
  return risk !== 'high' && risk !== 'critical'
}

/** La cara solo pide LECTURA o CONFIRMAR; LIBRE es cosa de la CLI de administración. */
export function userSelectable(mode: PermissionMode): mode is UserPermissionMode {
  return mode === 'lectura' || mode === 'confirmar'
}

/** Tiempo que le queda a LIBRE, "m:ss". `until` en segundos epoch, `nowMs` en ms. */
export function libreLeft(until: number | null | undefined, nowMs: number): string {
  if (!until) return ''
  const s = Math.max(0, Math.round(until - nowMs / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export type CancelNotice = { kind: 'caption' | 'error'; text: string }

/** Qué enseñar tras Stop. Nunca "completado" para algo cancelado o que falló. */
export function cancelNotice(c: Pick<CancelFrame, 'phase' | 'tool' | 'message'>): CancelNotice {
  if (c.phase === 'requested') return { kind: 'caption', text: 'Cancelando…' }
  if (c.phase === 'cancelled') return { kind: 'caption', text: c.message || 'Cancelado.' }
  return { kind: 'error', text: c.message || `No se pudo cancelar ${c.tool || 'la acción'}.` }
}

const FILLER_WORDS = new Set(
  ('como cómo que qué y o e u el la lo los las un una de del a al en con por se me ' +
    'eh ah oh mm mmm hm hmm um uh pues bueno este esto the and of to it uh-huh').split(' '),
)

/**
 * ¿Es ruido lo que ha oído el micrófono? Una palabra funcional suelta no lleva
 * intención y es justo lo que deja pasar un eco o un sonido de fondo: el filtro
 * de eco no descarta nunca una sola palabra. «sí», «no» y «para» no entran aquí.
 */
export function isNoiseUtterance(text: string): boolean {
  const words = text
    .toLowerCase()
    .replace(/[^\p{L}\s-]/gu, ' ')
    .split(/\s+/)
    .filter(Boolean)
  return words.length === 0 || (words.length === 1 && FILLER_WORDS.has(words[0]))
}

/**
 * Detector de frases repetidas: el reconocedor puede entregar dos veces la misma
 * frase y la segunda cortaría la respuesta a la primera. Devuelve true si
 * `text` es igual (sin mayúsculas ni signos) a la anterior dentro de `windowMs`.
 */
export function makeRepeatGuard(windowMs = 6000): (text: string, nowMs: number) => boolean {
  let last = ''
  let lastAt = -Infinity
  return (text, nowMs) => {
    const key = text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim()
    const repeat = key !== '' && key === last && nowMs - lastAt < windowMs
    last = key
    lastAt = nowMs
    return repeat
  }
}

/**
 * Ventana de escucha tras decir su nombre. Tiene un final fijo: lo que suene
 * de fondo (un vídeo, música) no la alarga. Solo volver a llamarlo la reabre.
 */
export function makeListenWindow(): {
  open: (ms: number, nowMs: number) => void
  remaining: (nowMs: number) => number
} {
  let until = 0
  return {
    open: (ms, nowMs) => {
      until = nowMs + ms
    },
    remaining: (nowMs) => Math.max(0, until - nowMs),
  }
}

/** ¿Sirve todavía el token de sesión, con margen para renovarlo antes de que caduque? */
export function tokenFresh(expiresAtMs: number, nowMs: number, marginMs = 60_000): boolean {
  return expiresAtMs - nowMs > marginMs
}
