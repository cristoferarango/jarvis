/**
 * Quién es el asistente y en qué idioma habla.
 *
 * Lo decide el núcleo (crisvis.toml → [asistente]) y llega en /health durante
 * el arranque; hasta entonces valen estos valores. La voz, el reconocedor y las
 * frases de relleno leen de aquí, así que cambiar el idioma o el nombre es una
 * línea de configuración y no una recompilación.
 */

export type Persona = {
  name: string
  /** BCP-47, p. ej. 'es-ES'. */
  lang: string
  /** Cómo se dirige al usuario: 'señor', 'señora', 'jefe'… */
  address: string
  /** Palabras que lo despiertan, en minúsculas. */
  wake: string[]
  /** 'original' = la voz británica del JARVIS original; 'nativa' = voz del idioma. */
  voiceStyle: 'original' | 'nativa'
}

let current: Persona = {
  name: 'Jarvis',
  lang: 'es-ES',
  address: 'señor',
  wake: ['jarvis', 'crisvis'],
  voiceStyle: 'original',
}

export function persona(): Persona {
  return current
}

export function setPersona(next: Partial<Persona>): void {
  current = {
    name: next.name?.trim() || current.name,
    lang: next.lang?.trim() || current.lang,
    address: next.address?.trim() || current.address,
    wake:
      next.wake && next.wake.length
        ? next.wake.map((w) => w.toLowerCase().trim()).filter(Boolean)
        : current.wake,
    voiceStyle:
      next.voiceStyle === 'nativa' || next.voiceStyle === 'original'
        ? next.voiceStyle
        : current.voiceStyle,
  }
  document.documentElement.lang = current.lang.split('-')[0]
}

/** ¿Habla español? Decide qué frases y qué variantes del nombre se usan. */
export function isSpanish(): boolean {
  return /^es\b/i.test(current.lang)
}

/** Variantes que los reconocedores suelen oír en lugar del nombre. */
const MISHEARINGS: Record<string, string[]> = {
  jarvis: ['jarvys', 'jervis', 'travis', 'jarviss', "java's", 'jarv', 'yarvis', 'harvis', 'charvis', 'jarbis', 'yarbis'],
  crisvis: ['chrisvis', 'crisbis', 'krisvis', 'crispis', 'crisvi', 'chris vis', 'cris vis'],
}

/** Patrón (sin anclar) que reconoce cualquiera de las palabras de activación. */
export function wakePattern(): string {
  const words = new Set<string>()
  for (const w of current.wake) {
    words.add(w)
    for (const alt of MISHEARINGS[w] ?? []) words.add(alt)
  }
  const escaped = [...words]
    .sort((a, b) => b.length - a.length)
    .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s*'))
  return `(?:${escaped.join('|')})`
}

/** Saludos que pueden ir delante del nombre: "hey jarvis", "oye crisvis". */
export const GREETING = '(?:hey|hi|ok|okay|yo|oye|eh|hola|vale)'
