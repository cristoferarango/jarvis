import { isSpanish, persona } from './persona'

/**
 * Frases de relleno.
 *
 * Una herramienta puede tardar diez segundos, y un silencio así parece un
 * cuelgue. Así que JARVIS dice algo en cuanto empieza el trabajo y luego calla
 * hasta tener la respuesta. Un acuse, sin parte de progreso.
 *
 * La gramática sigue al personaje, no al asistente genérico:
 *   - Las frases de trabajo son impersonales y breves: "Consultando.",
 *     "Cruzando datos." Nunca "voy a comprobarlo" ni "déjame ver".
 *   - Nada de "¡ahora mismo!": el acuse es deferente, no ansioso.
 *   - Sin muletillas, sin entusiasmo, sin disculpas, sin exclamaciones.
 *   - El tratamiento ("señor") al final es rutina; al principio sería alarma.
 *     Aquí todo es rutina, así que va al final y solo a veces.
 *
 * Las frases con {t} llevan el tratamiento configurado en el núcleo.
 */

type Pools = { working: string[]; acknowledge: string[]; attention: string[] }

const ES: Pools = {
  working: [
    'En ello, {t}.',
    'Consultando.',
    'Recuperando los datos.',
    'Accediendo al archivo.',
    'Cruzando datos.',
    'Lanzando la consulta.',
    'Buscando.',
    'En marcha.',
  ],
  acknowledge: ['Como desee, {t}.', 'Muy bien, {t}.', 'Entendido.', 'Considérelo hecho.', 'Enseguida, {t}.'],
  attention: ['¿Sí, {t}?', '¿{T}?', 'A su servicio, {t}.', 'Le escucho.', 'Dígame, {t}.'],
}

const EN: Pools = {
  working: [
    'Working on it, sir.',
    'Compiling.',
    'Retrieving.',
    'Accessing the archive.',
    'Cross-referencing.',
    'Running the query now.',
    'Searching.',
    'Under way.',
  ],
  acknowledge: ['As you wish, sir.', 'Very good, sir.', 'Certainly.', 'Understood.', 'Consider it done.'],
  attention: ['Yes, sir?', 'Sir?', 'At your service, sir.', 'Standing by.', 'Awake, sir.'],
}

const pools = () => (isSpanish() ? ES : EN)

function dress(line: string): string {
  const t = persona().address
  return line.replace('{t}', t).replace('{T}', t.charAt(0).toUpperCase() + t.slice(1))
}

/** Evita repetir la misma frase dos veces seguidas. */
function makePicker(pool: () => string[]) {
  let last = -1
  return () => {
    const list = pool()
    if (list.length < 2) return dress(list[0] ?? '')
    let i = last
    while (i === last) i = Math.floor(Math.random() * list.length)
    last = i
    return dress(list[i])
  }
}

export const working = makePicker(() => pools().working)
export const acknowledge = makePicker(() => pools().acknowledge)
export const attention = makePicker(() => pools().attention)

type Rule = {
  server?: RegExp
  tool?: RegExp
  es: string[]
  en: string[]
}

/**
 * Nombrar la tarea suena más cálido que un acuse genérico. Los nombres de
 * herramientas MCP son `mcp__<servidor>__<herramienta>` (o, ya embellecidos
 * para el HUD, `servidor · herramienta`), y cada mitad dice algo distinto:
 * se comparan por separado, y las reglas específicas van antes que las
 * generales.
 */
const BY_TOOL: Rule[] = [
  {
    tool: /búsqueda web|web_search|search|\bweb\b|crawl|research/,
    es: ['Buscando.', 'Consultando la red.'],
    en: ['Searching.', 'Consulting the record.'],
  },
  {
    tool: /memoria|memory/,
    es: ['Consultando la memoria.', 'Repasando lo que sé.'],
    en: ['Checking my notes.', 'Consulting memory.'],
  },
  {
    tool: /tiempo|weather/,
    es: ['Consultando el parte.', 'Mirando el cielo.'],
    en: ['Checking the forecast.'],
  },
  {
    tool: /calculadora|calculator/,
    es: ['Calculando.'],
    en: ['Calculating.'],
  },
  {
    tool: /terminal|shell|exec/,
    es: ['Ejecutando.', 'En la terminal.'],
    en: ['Executing.', 'At the terminal.'],
  },
  {
    tool: /archivo|file|pdf/,
    es: ['Abriendo el archivo.', 'Leyendo.'],
    en: ['Opening the file.', 'Reading.'],
  },
  {
    tool: /cámara|camera|look|watch/,
    es: ['Mirando.', 'Un vistazo.'],
    en: ['Looking.', 'Taking a look.'],
  },
  {
    tool: /enlace|probe|http|fetch/,
    es: ['Examinando el enlace.', 'Comprobando la página.'],
    en: ['Checking the link.'],
  },
  {
    tool: /calendar|\bmeeting\b|agenda/,
    es: ['Consultando la agenda.'],
    en: ['Checking your calendar.'],
  },
  {
    server: /gmail|\bmail\b/,
    tool: /gmail|\bmail\b|email|inbox|correo/,
    es: ['Revisando el correo.'],
    en: ['Checking your mail.'],
  },
  {
    server: /github|linear|jira|sentry|git/,
    tool: /\brepo\b|repository|\bissues?\b|pull_request|\bcommit\b|git/,
    es: ['Consultando el repositorio.'],
    en: ['Checking the repository.'],
  },
  {
    server: /^home|homeassistant|\bhue\b|\bhass\b/,
    tool: /\blights?\b|thermostat|\bdimmer\b|luz|luces/,
    es: ['Ajustándolo.', 'Me ocupo, {t}.'],
    en: ['Adjusting it now.'],
  },
]

const pickers = BY_TOOL.map((r) => ({
  ...r,
  pick: makePicker(() => (isSpanish() ? r.es : r.en)),
}))

function split(toolName: string): { server: string; tool: string } {
  const raw = /^mcp__(.+?)__(.+)$/.exec(toolName)
  if (raw) return { server: raw[1].toLowerCase(), tool: raw[2].toLowerCase() }
  const pretty = toolName.split(' · ')
  if (pretty.length === 2) return { server: pretty[0].toLowerCase(), tool: pretty[1].toLowerCase() }
  return { server: '', tool: toolName.toLowerCase() }
}

/** Una frase acorde a la herramienta que acaba de arrancar. */
export function forTool(toolName: string): string {
  const { server, tool } = split(toolName)
  for (const r of pickers) {
    const hit =
      (r.server !== undefined && server !== '' && r.server.test(server)) ||
      (r.tool !== undefined && r.tool.test(tool))
    if (hit) return r.pick()
  }
  return working()
}
