/**
 * Lógica pura del informe del día: qué enseña cada pestaña y en qué estado.
 *
 * Sin React ni DOM, para poder probarla con `node --test`. La cara solo pinta
 * lo que llega del núcleo: si una fuente no respondió, la pestaña lo dice; no
 * se rellena con nada inventado.
 */

import type {
  AdapterOverview,
  BriefingAlert,
  BriefingFrame,
  BriefingTab,
  ConnectorMode,
  ConnectorResult,
  ConnectorStatus,
  DailyBriefingRequest,
  OutcomeFrame,
  SourceState,
} from '@crisvis/protocol'

export const TABS: ReadonlyArray<{ id: BriefingTab; label: string }> = [
  { id: 'resumen', label: 'Resumen' },
  { id: 'correo', label: 'Correo' },
  { id: 'agenda', label: 'Agenda' },
  { id: 'tareas', label: 'Tareas' },
  { id: 'proyectos', label: 'Proyectos' },
  { id: 'negocio', label: 'Negocio' },
  { id: 'documentos', label: 'Documentos' },
  { id: 'sistema', label: 'Sistema' },
  { id: 'automatizaciones', label: 'Automatizaciones' },
  { id: 'integraciones', label: 'Integraciones' },
]

export const DEFAULT_TAB_SOURCES: Record<BriefingTab, string[]> = {
  resumen: [],
  correo: ['correo'],
  agenda: ['agenda'],
  tareas: ['tareas'],
  proyectos: ['github', 'gitlab'],
  negocio: ['crm'],
  documentos: ['documentos'],
  sistema: ['sistema', 'docker'],
  automatizaciones: ['n8n'],
  integraciones: [],
}

export type TabState = SourceState | 'loading' | 'idle'

export const STATE_LABEL: Record<TabState, string> = {
  success: 'Correcto',
  partial: 'Parcial',
  timeout: 'Sin respuesta',
  unauthorized: 'Sin autorización',
  unavailable: 'No disponible',
  error: 'Error',
  cancelled: 'Cancelado',
  loading: 'Consultando…',
  idle: 'Sin consultar',
}

export const MODE_LABEL: Record<ConnectorMode, string> = {
  no_configurado: 'No configurado',
  mock: 'Datos de prueba',
  sandbox: 'Sandbox',
  real: 'Real',
}

const EMPTY: Record<BriefingTab, string> = {
  resumen: 'Pide «Informe del día» para preparar el resumen.',
  correo: 'No hay correos que destacar.',
  agenda: 'No hay eventos para hoy.',
  tareas: 'No hay tareas abiertas.',
  proyectos: 'Sin actividad reciente en los repositorios.',
  negocio: 'No hay conversaciones abiertas.',
  documentos: 'No hay documentos recientes.',
  sistema: 'Sin datos del sistema.',
  automatizaciones: 'No hay ejecuciones recientes.',
  integraciones: 'Sin conectores registrados.',
}

/** Qué decir cuando una fuente no trajo datos, según por qué. Nunca el error crudo. */
const FAILURE_TEXT: Partial<Record<SourceState, string>> = {
  timeout: 'La fuente no respondió a tiempo. Puedes reintentar.',
  unauthorized: 'La conexión no está autorizada o se revocó. Revísala en Integraciones.',
  unavailable: 'La fuente no está disponible ahora mismo.',
  error: 'No se pudo consultar la fuente.',
  cancelled: 'La consulta se canceló.',
}

export type BriefingView = {
  open: boolean
  tab: BriefingTab
  running: boolean
  id: string | null
  request: DailyBriefingRequest | null
  tabs: Record<BriefingTab, string[]>
  connectors: ConnectorStatus[]
  results: Record<string, ConnectorResult>
  loading: string[]
  alerts: BriefingAlert[]
  voice: string
  error: string | null
  finishedAt: string | null
  overview: AdapterOverview | null
  audit: Record<string, unknown>[]
  outcomes: Record<string, OutcomeFrame>
}

export function emptyView(): BriefingView {
  return {
    open: false,
    tab: 'resumen',
    running: false,
    id: null,
    request: null,
    tabs: DEFAULT_TAB_SOURCES,
    connectors: [],
    results: {},
    loading: [],
    alerts: [],
    voice: '',
    error: null,
    finishedAt: null,
    overview: null,
    audit: [],
    outcomes: {},
  }
}

const LEVELS = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO'] as const

export function sortAlerts(alerts: BriefingAlert[]): BriefingAlert[] {
  return [...alerts].sort((a, b) => LEVELS.indexOf(a.level) - LEVELS.indexOf(b.level))
}

function allAlerts(results: Record<string, ConnectorResult>): BriefingAlert[] {
  return sortAlerts(Object.values(results).flatMap((r) => r.alerts))
}

function without(list: string[], item: string): string[] {
  return list.filter((x) => x !== item)
}

/** Aplicar una trama `briefing` del núcleo. Inmutable. */
export function applyFrame(view: BriefingView, frame: BriefingFrame): BriefingView {
  switch (frame.phase) {
    case 'start': {
      const tabs = frame.tabs ?? DEFAULT_TAB_SOURCES
      const sources = frame.request.sources.length
        ? frame.request.sources
        : Object.values(tabs).flat()
      return {
        ...view,
        open: true,
        running: true,
        request: frame.request,
        tabs,
        connectors: frame.connectors ?? view.connectors,
        results: {},
        loading: sources,
        alerts: [],
        voice: '',
        error: null,
        finishedAt: null,
        outcomes: {},
      }
    }
    case 'source': {
      const results = { ...view.results, [frame.result.source]: frame.result }
      return {
        ...view,
        results,
        loading: without(view.loading, frame.result.source),
        alerts: allAlerts(results),
        connectors: view.connectors.map((c) =>
          c.id === frame.result.source
            ? {
                ...c,
                state: frame.result.state,
                message: frame.result.message,
                checked_at: frame.result.fetched_at,
              }
            : c,
        ),
      }
    }
    case 'done': {
      const results: Record<string, ConnectorResult> = {}
      for (const r of frame.result.results) results[r.source] = r
      return {
        ...view,
        running: false,
        id: frame.result.id,
        results,
        loading: [],
        alerts: sortAlerts(frame.result.alerts),
        voice: frame.result.voice_summary,
        finishedAt: frame.result.finished_at,
      }
    }
    case 'cancelled':
      return { ...view, running: false, loading: [], error: 'Informe cancelado.' }
    case 'error':
      return {
        ...view,
        running: frame.source ? view.running : false,
        loading: frame.source ? without(view.loading, frame.source) : [],
        error: frame.message,
      }
    case 'status':
      return { ...view, overview: frame.overview, connectors: frame.overview.connectors }
    case 'audit':
      return { ...view, audit: frame.events }
  }
}

export function sourcesFor(view: BriefingView, tab: BriefingTab): string[] {
  return view.tabs[tab] ?? DEFAULT_TAB_SOURCES[tab] ?? []
}

const WORST: SourceState[] = [
  'error',
  'unauthorized',
  'timeout',
  'unavailable',
  'cancelled',
  'partial',
  'success',
]

/**
 * Estado de una pestaña. Si todas sus fuentes van bien, «Correcto»; si alguna
 * falla pero otra respondió, «Parcial»; si fallan todas, el peor fallo.
 */
export function tabState(view: BriefingView, tab: BriefingTab): TabState {
  const sources = sourcesFor(view, tab)
  if (tab === 'resumen' || tab === 'integraciones') {
    if (view.running) return 'loading'
    if (!Object.keys(view.results).length) return 'idle'
    const states = Object.values(view.results).map((r) => r.state)
    return combine(states)
  }
  if (sources.some((s) => view.loading.includes(s))) return 'loading'
  const states = sources.map((s) => view.results[s]?.state).filter(Boolean) as SourceState[]
  if (!states.length) return 'idle'
  return combine(states)
}

function combine(states: SourceState[]): SourceState {
  if (states.every((s) => s === 'success')) return 'success'
  const ok = states.some((s) => s === 'success' || s === 'partial')
  if (ok) return 'partial'
  return [...states].sort((a, b) => WORST.indexOf(a) - WORST.indexOf(b))[0]
}

export function resultsFor(view: BriefingView, tab: BriefingTab): ConnectorResult[] {
  return sourcesFor(view, tab)
    .map((s) => view.results[s])
    .filter((r): r is ConnectorResult => Boolean(r))
}

export function alertsFor(view: BriefingView, tab: BriefingTab): BriefingAlert[] {
  if (tab === 'resumen') return view.alerts
  const sources = new Set(sourcesFor(view, tab))
  return view.alerts.filter((a) => sources.has(a.source))
}

export function lastUpdate(view: BriefingView, tab: BriefingTab): string | null {
  const dates = (tab === 'resumen' || tab === 'integraciones'
    ? Object.values(view.results)
    : resultsFor(view, tab)
  ).map((r) => r.fetched_at)
  if (!dates.length) return null
  return dates.sort().at(-1) ?? null
}

export function emptyText(tab: BriefingTab): string {
  return EMPTY[tab]
}

/** Texto seguro para una fuente que no trajo datos. */
export function failureText(result: ConnectorResult): string {
  if (result.state === 'success' || result.state === 'partial') return ''
  return FAILURE_TEXT[result.state] ?? 'No se pudo consultar la fuente.'
}

export function isMock(view: BriefingView): boolean {
  return Object.values(view.results).some((r) => r.mode === 'mock')
}

/** «08:42» en la zona del informe. */
export function clock(iso: string | null | undefined, timeZone = 'America/Lima'): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  try {
    return new Intl.DateTimeFormat('es-PE', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
      timeZone,
    }).format(date)
  } catch {
    return date.toISOString().slice(11, 16)
  }
}

/** Contadores de la pestaña Resumen, sacados de lo que devolvieron las fuentes. */
export function summaryCounts(view: BriefingView): Array<{ label: string; value: number | null }> {
  const r = view.results
  const ok = (s: string) => r[s] && (r[s].state === 'success' || r[s].state === 'partial')
  const count = (s: string, key: string) => (ok(s) ? (r[s].counts[key] ?? 0) : null)
  return [
    { label: 'Correos prioritarios', value: count('correo', 'prioritarios') },
    { label: 'Reuniones', value: count('agenda', 'reuniones') },
    { label: 'Tareas vencidas', value: count('tareas', 'vencidas') },
    {
      label: 'Alertas críticas/altas',
      value: Object.keys(r).length
        ? view.alerts.filter((a) => a.level === 'CRITICAL' || a.level === 'HIGH').length
        : null,
    },
  ]
}
