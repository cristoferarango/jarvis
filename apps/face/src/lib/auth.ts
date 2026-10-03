/**
 * Sesión de la cara con el núcleo.
 *
 * El núcleo fija una cookie de arranque al servir la página; con ella,
 * `POST /api/sesion` da un token corto que acompaña a /tts, /stt y /api/*, y
 * `POST /api/sesion/ticket` da el ticket de un solo uso para abrir el
 * WebSocket. Si el núcleo se reinició, la cookie es de otro arranque: se
 * recarga la página una vez para recibir la nueva.
 */
import { CORE_HTTP_URL } from '../config'
import { tokenFresh } from './guardrails'

const RELOAD_KEY = 'crisvis.reloadedForSession'

type Token = { value: string; expiresAt: number }

let token: Token | null = null
let inflight: Promise<string> | null = null

export class AuthError extends Error {}

async function requestToken(): Promise<string> {
  const headers: Record<string, string> = {}
  if (token) headers['X-Crisvis-Token'] = token.value
  const res = await fetch(`${CORE_HTTP_URL}/api/sesion`, {
    method: 'POST',
    credentials: 'same-origin',
    headers,
  })
  if (res.status === 401) {
    const body = (await res.json().catch(() => ({}))) as { reload?: boolean }
    token = null
    if (body.reload && !sessionStorage.getItem(RELOAD_KEY)) {
      sessionStorage.setItem(RELOAD_KEY, '1')
      location.reload()
    }
    throw new AuthError('El núcleo no reconoce esta página; recárgala.')
  }
  if (!res.ok) throw new AuthError(`El núcleo rechazó la sesión (${res.status}).`)
  const data = (await res.json()) as { token: string; expiresIn: number }
  sessionStorage.removeItem(RELOAD_KEY)
  token = { value: data.token, expiresAt: Date.now() + data.expiresIn * 1000 }
  return token.value
}

/** El token vigente, pidiendo o renovando uno si hace falta. */
export async function sessionToken(): Promise<string> {
  if (token && tokenFresh(token.expiresAt, Date.now())) return token.value
  if (!inflight) {
    inflight = requestToken().finally(() => {
      inflight = null
    })
  }
  return inflight
}

/** Olvidar el token (el núcleo lo rechazó o se reinició). */
export function invalidate(): void {
  token = null
}

/** Cabeceras para /tts, /stt y /api/*. */
export async function authHeaders(): Promise<Record<string, string>> {
  return { 'X-Crisvis-Token': await sessionToken() }
}

/** fetch con token; un 401 se reintenta una vez con un token nuevo. */
export async function authFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const go = async () =>
    fetch(`${CORE_HTTP_URL}${path}`, {
      ...init,
      credentials: 'same-origin',
      headers: { ...(init.headers as Record<string, string> | undefined), ...(await authHeaders()) },
    })
  let res = await go()
  if (res.status === 401) {
    invalidate()
    res = await go()
  }
  return res
}

/** Ticket de un solo uso para /ws. */
export async function wsTicket(): Promise<string> {
  const res = await authFetch('/api/sesion/ticket', { method: 'POST' })
  if (!res.ok) throw new AuthError(`Sin ticket para el núcleo (${res.status}).`)
  const data = (await res.json()) as { ticket: string }
  return data.ticket
}
