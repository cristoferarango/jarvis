import { useStore } from '../store'
import type { ConfirmRequest } from './brain'

/**
 * Peticiones de permiso a la espera del usuario.
 *
 * El núcleo ya decidió que esta acción necesita un sí explícito; aquí solo se
 * recoge, por botón o por voz. Una sola a la vez: si llega otra, la anterior se
 * da por denegada. Si nadie contesta antes del plazo del núcleo, también.
 */

let resolver: ((approved: boolean) => void) | null = null

export function requestConfirm(req: ConfirmRequest): Promise<boolean> {
  answerConfirm(false)
  return new Promise<boolean>((resolve) => {
    const timer = window.setTimeout(() => settle(false), req.seconds * 1000)
    const settle = (approved: boolean) => {
      if (resolver !== settle) return
      window.clearTimeout(timer)
      resolver = null
      useStore.getState().setConfirm(null)
      resolve(approved)
    }
    resolver = settle
    useStore.getState().setConfirm({
      id: req.id,
      tool: req.tool,
      tier: req.tier,
      summary: req.summary,
      deadline: Date.now() + req.seconds * 1000,
    })
  })
}

export function answerConfirm(approved: boolean): void {
  resolver?.(approved)
}

export const confirmPending = () => resolver !== null

const YES =
  /^(?:s[ií]|vale|venga|adelante|hazlo|h[aá]galo|procede|proceda|claro|de acuerdo|ok|okay|okey|confirmo|confirmado|autorizo|autorizado|permitido|yes|yeah|sure|go ahead|do it|approve)(?![\p{L}])/iu
const NO =
  /^(?:no|nada|cancela\p{L}*|para|alto|deniega|denegado|mejor no|ni hablar|espera|stop|cancel|don'?t|deny)(?![\p{L}])/iu

/** "sí, adelante" -> true, "no, espera" -> false, cualquier otra cosa -> null. */
export function parseAnswer(said: string, wake: RegExp): boolean | null {
  const text = said
    .toLowerCase()
    .replace(wake, ' ')
    .replace(/^[\s,.:;¡!¿?-]+/u, '')
    .trim()
  // A real answer is a few words. Anything longer is more likely his own
  // prompt echoing back through the microphone than a decision.
  if (text.split(/\s+/).length > 6) return null
  if (NO.test(text)) return false
  if (YES.test(text)) return true
  return null
}
