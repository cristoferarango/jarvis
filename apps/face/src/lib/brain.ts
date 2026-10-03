import type {
  ActionRisk,
  ApprovalRequest,
  BrainStatus,
  BriefingFrame,
  CancelFrame,
  CaptureFrame,
  ConfirmFrame,
  InputSource,
  OutcomeFrame,
  PermissionMode,
  ReadyFrame,
  StatusFrame,
  UserPermissionMode,
} from '@crisvis/protocol'
import { PROTOCOL_VERSION } from '@crisvis/protocol'
import type { Blade, Panel } from '../store'
import { CORE_WS_URL } from '../config'
import { authFetch, invalidate, wsTicket } from './auth'

/**
 * Cliente del núcleo de Crisvis.
 *
 * Un único WebSocket lleva todo: las preguntas y sus respuestas en streaming,
 * y lo que el cerebro empuja a mitad de turno (paneles, blades, órdenes de
 * interfaz, peticiones de cámara y de confirmación).
 *
 * A diferencia del puente original, el socket NO es la conversación: la sesión
 * vive en el núcleo con un identificador que guardamos aquí. Si el socket cae,
 * al volver se presenta con ese identificador (`hello`) y la charla sigue donde
 * estaba.
 */

export type AskHandlers = {
  onText: (delta: string) => void
  onTool: (name: string) => void
  /** El núcleo aceptó la entrada: solo entonces se muestra como del usuario. */
  onAccepted?: () => void
}

export type { BrainStatus, InputSource, PermissionMode }

/** Todo lo que puede llegar. Laxo a propósito: una trama de una versión futura
 *  del núcleo se ignora, no rompe el turno. */
type Frame = {
  type?: string
  delta?: string
  name?: string
  text?: string
  message?: string
  panel?: Panel
  blade?: Blade
  op?: string
  args?: unknown
  id?: string
  ask?: string | null
  reason?: string
  mode?: string
  seconds?: number
  when?: string
  servers?: Array<string | { name?: string }>
  session?: string
  resumed?: boolean
  brain?: BrainStatus
  permissions?: { mode: PermissionMode; libreUntil?: number | null }
  tool?: string
  tier?: ConfirmFrame['tier']
  risk?: ActionRisk
  warning?: string
  target?: string
  cancellable?: boolean
  grant?: string
  nonce?: string
  summary?: string
  phase?: string
  request?: unknown
  proposal?: string
  accepted?: boolean
  exec?: string
}

const SESSION_KEY = 'crisvis.session'

let askSeq = 0
let socket: WebSocket | null = null
let connecting: Promise<WebSocket> | null = null

// ---------------------------------------------------------------------------
// Estado publicado por el núcleo
// ---------------------------------------------------------------------------

export type CoreStatus = {
  servers: string[]
  brain: BrainStatus | null
  permissions: PermissionMode
  /** Epoch (s) en que LIBRE caduca, si está activo. */
  libreUntil: number | null
}

let status: CoreStatus = { servers: [], brain: null, permissions: 'confirmar', libreUntil: null }
let onStatus: ((s: CoreStatus) => void) | null = null

/** Estado del cerebro, servidores MCP y modo de permisos. Llega al conectar y
 *  cada vez que cambia (el motor arranca, alguien cambia el modo…). */
export function watchStatus(fn: (s: CoreStatus) => void) {
  onStatus = fn
  fn(status)
}
export const coreStatus = () => status

function applyStatus(msg: ReadyFrame | StatusFrame | Frame) {
  const servers = (msg.servers ?? [])
    .map((s) => (typeof s === 'string' ? s : (s.name ?? '')))
    .filter(Boolean)
  status = {
    servers,
    brain: (msg.brain as BrainStatus | undefined) ?? status.brain,
    permissions: (msg.permissions?.mode as PermissionMode | undefined) ?? status.permissions,
    libreUntil: msg.permissions ? (msg.permissions.libreUntil ?? null) : status.libreUntil,
  }
  onStatus?.(status)
}

// ---------------------------------------------------------------------------
// Canales empujados por el núcleo
// ---------------------------------------------------------------------------

let onPanel: ((panel: Panel) => void) | null = null
export function watchPanels(fn: (panel: Panel) => void) {
  onPanel = fn
}

let onBlade: ((blade: Blade) => void) | null = null
export function watchBlades(fn: (blade: Blade) => void) {
  onBlade = fn
}

let onUi: ((op: string, args: any) => void) | null = null
export function watchUi(fn: (op: string, args: any) => void) {
  onUi = fn
}

export type CaptureRequest = Pick<CaptureFrame, 'mode' | 'reason' | 'seconds' | 'when'>
export type CaptureResult = { data?: string; mimeType?: string; error?: string }

let onCapture: ((req: CaptureRequest) => Promise<CaptureResult>) | null = null
export function watchCapture(fn: (req: CaptureRequest) => Promise<CaptureResult>) {
  onCapture = fn
}

/**
 * Peticiones de permiso: el cerebro quiere hacer algo que el modo actual
 * manda confirmar (escribir un archivo, ejecutar una orden…). La respuesta es
 * un sí o un no; si nadie contesta a tiempo, el núcleo lo trata como un no.
 */
export type ConfirmRequest = {
  id: string
  tool: string
  tier: ConfirmFrame['tier']
  risk: ActionRisk
  summary: string
  warning: string
  target: string
  cancellable: boolean
  seconds: number
}
let onConfirm: ((req: ConfirmRequest) => Promise<boolean>) | null = null
export function watchConfirm(fn: (req: ConfirmRequest) => Promise<boolean>) {
  onConfirm = fn
}

/** Stop sobre una herramienta ya en marcha: Cancelando… → Cancelado / No se pudo. */
export type CancelStatus = Pick<CancelFrame, 'phase' | 'tool' | 'cancellable' | 'message'>
let onCancel: ((s: CancelStatus) => void) | null = null
export function watchCancel(fn: (s: CancelStatus) => void) {
  onCancel = fn
}

/**
 * Informe del día (OpenClawAdapter). La cara nunca habla con OpenClaw: pide
 * al núcleo y el núcleo pasa por el adaptador.
 */
let onBriefing: ((frame: BriefingFrame) => void) | null = null
export function watchBriefing(fn: (frame: BriefingFrame) => void) {
  onBriefing = fn
}

/**
 * Aprobación exacta de una acción del informe. La respuesta lleva la huella de
 * la acción que se mostró: si la acción cambia, la aprobación no vale.
 */
let onApproval: ((req: ApprovalRequest) => Promise<boolean>) | null = null
export function watchApproval(fn: (req: ApprovalRequest) => Promise<boolean>) {
  onApproval = fn
}

let onOutcome: ((frame: OutcomeFrame) => void) | null = null
export function watchOutcome(fn: (frame: OutcomeFrame) => void) {
  onOutcome = fn
}

/** Pestañas del informe: refrescar una fuente, estado de conectores o auditoría. */
export function requestBriefing(action: 'refresh' | 'status' | 'audit', source?: string): void {
  if (socket) sendRaw(socket, { type: 'briefing', action, ...(source ? { source } : {}) })
}

/** Pedir que el núcleo evalúe una acción propuesta. Puede volver una `approval`. */
export function proposeAction(proposal: string): void {
  if (socket) sendRaw(socket, { type: 'propose', proposal })
}

/**
 *   'open'        — primera conexión de la página.
 *   'lost'        — el socket cayó; reintentando.
 *   'resumed'     — de vuelta, y el núcleo conservaba la conversación.
 *   'reconnected' — de vuelta, pero en una sesión nueva (el núcleo se reinició).
 */
export type ConnectionState = 'open' | 'lost' | 'resumed' | 'reconnected'
let onConnection: ((state: ConnectionState) => void) | null = null
export function watchConnection(fn: (state: ConnectionState) => void) {
  onConnection = fn
}

export function isConnected(): boolean {
  return socket?.readyState === WebSocket.OPEN
}

// ---------------------------------------------------------------------------
// Conexión
// ---------------------------------------------------------------------------

function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

let firstReady = deferred()
let everConnected = false
let wasLost = false

const RECONNECT_DELAYS = [500, 1000, 2000, 4000, 8000, 8000, 15000, 15000, 30000]
let attempt = 0
let reconnectTimer = 0

function scheduleReconnect() {
  if (attempt >= RECONNECT_DELAYS.length) return
  const delay = RECONNECT_DELAYS[attempt]
  attempt += 1
  clearTimeout(reconnectTimer)
  reconnectTimer = window.setTimeout(() => {
    void connect().catch(() => {})
  }, delay)
}

function sendRaw(ws: WebSocket, frame: Record<string, unknown>) {
  if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(frame))
}

/** Un único oyente por socket para todo lo que no pertenece a un turno. */
function dispatch(ws: WebSocket) {
  ws.addEventListener('message', (e: MessageEvent) => {
    let msg: Frame
    try {
      msg = JSON.parse(e.data as string)
    } catch {
      return
    }

    switch (msg.type) {
      case 'ready': {
        const previous = sessionStorage.getItem(SESSION_KEY)
        if (msg.session) sessionStorage.setItem(SESSION_KEY, msg.session)
        applyStatus(msg)
        if (msg.resumed) {
          onConnection?.('resumed')
        } else if (previous && previous !== msg.session && wasLost) {
          // El primer `ready` llega antes de que el núcleo vea nuestro hello;
          // si la sesión se retoma llegará un segundo `ready` con resumed.
          window.setTimeout(() => {
            if (sessionStorage.getItem(SESSION_KEY) === msg.session && wasLost) {
              wasLost = false
              onConnection?.('reconnected')
            }
          }, 600)
        }
        if (msg.resumed) wasLost = false
        firstReady.resolve()
        break
      }
      case 'status':
        applyStatus(msg)
        break
      case 'panel':
        if (msg.panel) onPanel?.(msg.panel)
        break
      case 'blade':
        if (msg.blade) onBlade?.(msg.blade)
        break
      case 'ui':
        if (msg.op) onUi?.(msg.op, (msg.args ?? {}) as Record<string, unknown>)
        break
      case 'capture': {
        if (!msg.id) break
        const id = msg.id
        const reply = (payload: Record<string, unknown>) =>
          sendRaw(ws, { type: 'reply', id, ...payload })
        if (!onCapture) {
          reply({ error: 'La interfaz no tiene cámara.' })
          break
        }
        // Siempre responde, también al fallar: el núcleo tiene un turno
        // esperando esta respuesta.
        onCapture({
          mode: msg.mode === 'watch' ? 'watch' : 'look',
          reason: msg.reason ?? '',
          seconds: Math.max(2, Math.min(15, Number(msg.seconds) || 6)),
          when: msg.when === 'past' ? 'past' : 'now',
        })
          .then(reply)
          .catch((err) => reply({ error: String(err?.message ?? err) }))
        break
      }
      case 'confirm': {
        if (!msg.id) break
        const id = msg.id
        // La aprobación es de un solo uso y solo para esta acción: se devuelven
        // tal cual el id y el nonce que mandó el núcleo.
        const grant = msg.grant ?? ''
        const nonce = msg.nonce ?? ''
        const answer = (approved: boolean) =>
          sendRaw(ws, { type: 'reply', id, approved, ...(approved ? { grant, nonce } : {}) })
        if (!onConfirm) {
          answer(false)
          break
        }
        onConfirm({
          id,
          tool: msg.tool ?? '',
          tier: msg.tier ?? 'escritura',
          risk: msg.risk ?? 'medium',
          summary: msg.summary ?? msg.tool ?? '',
          warning: msg.warning ?? '',
          target: msg.target ?? '',
          cancellable: msg.cancellable === true,
          seconds: Number(msg.seconds) || 30,
        })
          .then(answer)
          .catch(() => answer(false))
        break
      }
      case 'briefing':
        if (msg.phase) onBriefing?.(msg as unknown as BriefingFrame)
        break
      case 'approval': {
        if (!msg.id || !msg.request) break
        const id = msg.id
        const request = msg.request as ApprovalRequest
        const answer = (approved: boolean) =>
          sendRaw(ws, { type: 'reply', id, approved, fingerprint: request.fingerprint })
        if (!onApproval) {
          answer(false)
          break
        }
        onApproval(request)
          .then(answer)
          .catch(() => answer(false))
        break
      }
      case 'outcome':
        if (msg.proposal) onOutcome?.(msg as unknown as OutcomeFrame)
        break
      case 'cancel':
        if (msg.phase) {
          onCancel?.({
            phase: msg.phase as CancelFrame['phase'],
            tool: msg.tool ?? '',
            cancellable: msg.cancellable === true,
            message: msg.message ?? '',
          })
        }
        break
    }
  })
}

/** Cierres del núcleo con significado (ver crisvis.gateway.ws). */
const CLOSE_PERMISSIONS_CHANGED = 4001

function connect(): Promise<WebSocket> {
  if (socket?.readyState === WebSocket.OPEN) return Promise.resolve(socket)
  if (connecting) return connecting

  firstReady = deferred()

  connecting = (async () => {
    // Ticket de un solo uso, pedido con el token de sesión (ver lib/auth.ts).
    const ticket = await wsTicket()
    return open(`${CORE_WS_URL}?ticket=${encodeURIComponent(ticket)}`)
  })()
  connecting.catch(() => {
    connecting = null
    if (everConnected) scheduleReconnect()
  })
  return connecting
}

function open(url: string): Promise<WebSocket> {
  return new Promise<WebSocket>((resolve, reject) => {
    const ws = new WebSocket(url)
    let settled = false

    const settle = (err: Error | null) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      connecting = null
      if (err) reject(err)
      else resolve(ws)
    }

    const timer = setTimeout(() => {
      ws.close()
      settle(new Error('El núcleo no responde. ¿Está en marcha `crisvis`?'))
    }, 6000)

    ws.onopen = () => {
      socket = ws
      attempt = 0
      dispatch(ws)
      // `hello` siempre primero: el núcleo rechaza cualquier otra trama antes.
      const previous = sessionStorage.getItem(SESSION_KEY) ?? ''
      sendRaw(ws, { type: 'hello', session: previous, protocol: PROTOCOL_VERSION })
      settle(null)
      if (!everConnected) onConnection?.('open')
      everConnected = true
    }
    ws.onerror = () => {
      // El navegador no dice por qué: un núcleo apagado y un origen rechazado
      // llegan aquí igual.
      settle(
        new Error(
          `No puedo conectar con el núcleo en ${CORE_WS_URL}. O no está en marcha ` +
            '(arráncalo con `npm start`), o esta página se sirve desde un origen que ' +
            'no acepta.',
        ),
      )
    }
    ws.onclose = (e: CloseEvent) => {
      settle(new Error('El núcleo cerró la conexión.'))
      // Un rechazo del ticket u origen: el token puede ser de otro arranque.
      if (!socket || socket !== ws) invalidate()
      if (socket === ws) {
        socket = null
        wasLost = true
        onConnection?.('lost')
        // Cambió el modo de permisos: reconectar ya, con un ticket nuevo.
        if (e.code === CLOSE_PERMISSIONS_CHANGED) attempt = 0
        scheduleReconnect()
      }
    }
  })
}

/** Abrir el socket pronto, para que el primer "Jarvis" no espere al handshake. */
export async function warm(): Promise<void> {
  await connect()
  await Promise.race([
    firstReady.promise,
    new Promise<void>((resolve) => setTimeout(resolve, 2500)),
  ])
}

/**
 * Bajar a SOLO LECTURA o volver a CONFIRMAR. Va por HTTP con el token, nunca
 * por el WebSocket; LIBRE no se puede pedir desde aquí (solo la CLI de
 * administración). El núcleo cierra los sockets y la cara reconecta con el
 * estado nuevo.
 */
export async function setPermissions(mode: UserPermissionMode): Promise<void> {
  const res = await authFetch('/api/permisos', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ modo: mode }),
  })
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string }
    throw new Error(body.detail ?? `El núcleo rechazó el cambio (${res.status}).`)
  }
  const data = (await res.json()) as { mode: PermissionMode; libreUntil: number | null }
  status = { ...status, permissions: data.mode, libreUntil: data.libreUntil }
  onStatus?.(status)
}

// ---------------------------------------------------------------------------
// Turnos
// ---------------------------------------------------------------------------

/** Dos minutos sin ninguna trama: ese turno no va a volver. Generoso porque
 *  un modelo local puede pensar un rato y una confirmación puede esperar. */
const IDLE_TIMEOUT_MS = 120_000

let pending: { finish: (fallback?: string) => void } | null = null

export async function ask(
  prompt: string,
  handlers: AskHandlers,
  source: InputSource = 'desconocido',
): Promise<{ text: string; tools: string[] }> {
  // Una pregunta nueva sustituye a la que esté en vuelo.
  if (pending) cancel()

  let cancelledWhileDialling = false
  pending = {
    finish: () => {
      cancelledWhileDialling = true
    },
  }

  let ws: WebSocket
  try {
    ws = await connect()
  } catch (err) {
    pending = null
    throw err
  }

  if (cancelledWhileDialling) {
    pending = null
    return { text: '', tools: [] }
  }

  const id = `a${++askSeq}`
  const tools: string[] = []
  let text = ''

  return new Promise((resolve, reject) => {
    let done = false
    let timer = 0

    const cleanup = () => {
      done = true
      pending = null
      clearTimeout(timer)
      ws.removeEventListener('message', onMessage)
      ws.removeEventListener('close', onClose)
    }

    const finish = (fallback = '') => {
      if (done) return
      cleanup()
      resolve({ text: (text || fallback).trim(), tools })
    }

    const fail = (err: Error) => {
      if (done) return
      cleanup()
      reject(err)
    }

    const arm = () => {
      clearTimeout(timer)
      timer = window.setTimeout(() => {
        fail(new Error('El núcleo se quedó callado; ese turno se perdió.'))
      }, IDLE_TIMEOUT_MS)
    }

    const onMessage = (e: MessageEvent) => {
      arm()
      let msg: Frame
      try {
        msg = JSON.parse(e.data as string)
      } catch {
        return
      }
      // La respuesta de otra pregunta (una que se abandonó).
      if (msg.ask && msg.ask !== id) return
      if (msg.type === 'confirm') {
        // Esperando al usuario: no es silencio del núcleo.
        clearTimeout(timer)
        return
      }
      if (!('ask' in msg)) return

      try {
        switch (msg.type) {
          case 'input':
            if (msg.accepted) handlers.onAccepted?.()
            else fail(new Error(msg.message ?? 'El núcleo rechazó la entrada.'))
            break
          case 'text':
            text += msg.delta ?? ''
            handlers.onText(msg.delta ?? '')
            break
          case 'tool':
            if (!msg.name) break
            tools.push(msg.name)
            handlers.onTool(prettyToolName(msg.name))
            break
          case 'done':
            finish(msg.text ?? '')
            break
          case 'error':
            fail(new Error(msg.message ?? 'El núcleo informó de un error.'))
            break
        }
      } catch (err) {
        fail(err instanceof Error ? err : new Error(String(err)))
      }
    }

    const onClose = () => {
      fail(new Error('Se cortó la conexión a mitad de respuesta.'))
    }

    pending = { finish }
    ws.addEventListener('message', onMessage)
    ws.addEventListener('close', onClose)
    arm()

    try {
      ws.send(JSON.stringify({ type: 'ask', text: prompt, id, source }))
    } catch (err) {
      fail(err instanceof Error ? err : new Error(String(err)))
    }
  })
}

/** Cortarle a mitad de respuesta. Devuelve lo que ya había dicho. */
export function cancel(): void {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: 'interrupt' }))
  }
  pending?.finish()
}

export function interrupt(): void {
  cancel()
}

/** Etiquetas para el carril SYSTEMS del HUD: motor, modelo, memoria y MCP. */
export function connectedLabels(): string[] {
  const b = status.brain
  const labels: string[] = []
  if (b?.ok) {
    labels.push(b.engine ? b.engine.toUpperCase() : 'MOTOR')
    if (b.model) labels.push(b.model)
    if (b.memory) labels.push('MEMORIA')
    if (b.visionModel) labels.push('VISIÓN')
  }
  return [...labels, ...status.servers]
}

const TOOL_LABELS: Record<string, string> = {
  web_search: 'búsqueda web',
  calculator: 'calculadora',
  get_weather: 'tiempo',
  file_read: 'leer archivo',
  file_write: 'escribir archivo',
  shell_exec: 'terminal',
  memory_search: 'memoria',
  memory_store: 'memoria',
  memory_retrieve: 'memoria',
  probe_url: 'examinar enlace',
  look: 'cámara',
  watch: 'cámara',
  http_request: 'petición web',
  pdf_extract: 'leer PDF',
}

/** `mcp__srv__do_thing` -> `srv · do thing`; nombres conocidos, en castellano. */
function prettyToolName(raw: string): string {
  if (TOOL_LABELS[raw]) return TOOL_LABELS[raw]
  if (!raw.startsWith('mcp__')) return raw.replace(/_/g, ' ')
  const [, server, ...rest] = raw.split('__')
  return `${server} · ${rest.join(' ').replace(/_/g, ' ')}`
}
