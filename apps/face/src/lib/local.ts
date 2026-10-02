import { CORE_HTTP_URL } from '../config'

/**
 * Rutas que están de verdad en el disco de esta máquina, frente a URLs
 * relativas de la app que casualmente empiezan por barra (`/vite.svg` es un
 * recurso propio; `/Users/tu/captura.png` o `C:\Users\tu\captura.png` es una
 * imagen que acaba de aparecer en disco).
 *
 * Única definición, compartida por el saneador, las cuchillas y las órbitas.
 */
const UNIX_DISK =
  /^\/(Users|home|root|Volumes|Applications|System|Library|private|tmp|var|opt|mnt|media|srv|data)\//
const WINDOWS_DISK = /^[a-zA-Z]:[\\/]/

/** La ruta de disco que representa `raw`, o null si no es una. */
export function diskPath(raw: string): string | null {
  const path = String(raw ?? '')
    .trim()
    .replace(/^file:\/\//i, '')
    .replace(/^\/([a-zA-Z]:[\\/])/, '$1')
  if (!UNIX_DISK.test(path) && !WINDOWS_DISK.test(path)) return null
  try {
    return decodeURI(path)
  } catch {
    return path
  }
}

/** URL absoluta o relativa del núcleo para una ruta como `/img`. */
export const coreUrl = (route: string) => `${CORE_HTTP_URL}${route}`

/** ¿La URL apunta ya al núcleo? Proxificar el proxy le pediría buscarse a sí mismo. */
export function isCoreUrl(raw: string): boolean {
  if (CORE_HTTP_URL && raw.startsWith(`${CORE_HTTP_URL}/`)) return true
  return typeof location !== 'undefined' && raw.startsWith(`${location.origin}/`)
}

/** Pasa una fuente por el núcleo: disco a `/file`, http(s) remoto a `/img` o `/media`. */
export function viaCore(raw: string, route: 'img' | 'media'): string {
  const src = String(raw ?? '').trim()
  if (!src) return ''
  const path = diskPath(src)
  if (path) return `${coreUrl('/file')}?path=${encodeURIComponent(path)}`
  if (!/^https?:\/\//i.test(src)) return src
  if (isCoreUrl(src)) return src
  return `${coreUrl(`/${route}`)}?url=${encodeURIComponent(src)}`
}
