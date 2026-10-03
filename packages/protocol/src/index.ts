/**
 * Contrato del WebSocket entre la cara (apps/face) y el núcleo (src/crisvis).
 *
 * Los nombres de trama se comprueban contra ../frames.json (npm test), igual
 * que el lado Python (tests/test_protocol.py). Cambiar una trama aquí sin
 * cambiarla allí rompe la comprobación, que es justo la idea.
 */

export const PROTOCOL_VERSION = 2

export type PermissionMode = 'lectura' | 'confirmar' | 'libre'
/** Modos que la interfaz puede pedir. LIBRE solo lo habilita la CLI de administración. */
export type UserPermissionMode = 'lectura' | 'confirmar'
/** Riesgo de una acción concreta del agente (crisvis.security.guard). */
export type ActionRisk = 'low' | 'medium' | 'high' | 'critical'
/** De dónde viene una entrada del usuario (telemetría, sin contenido). */
export type InputSource = 'voz' | 'voz_activacion' | 'teclado' | 'boton' | 'desconocido'

export type BrainStatus = {
  ok: boolean
  engine: string | null
  model: string | null
  visionModel: string | null
  tools: string[]
  /** Backend de memoria en uso, o null si está desactivada. */
  memory: string | null
  message: string
}

export type PermissionStatus = {
  mode: PermissionMode
  /** Hora (epoch, s) a la que LIBRE vuelve solo a CONFIRMAR. */
  libreUntil?: number | null
  libreAvailable?: boolean
}

// -- cara -> núcleo ----------------------------------------------------------
// El WebSocket se abre con /ws?ticket=… (POST /api/sesion/ticket). La primera
// trama es siempre `hello`. El modo de permisos NO viaja por aquí.

export type HelloFrame = { type: 'hello'; session: string; protocol: number }
export type AskFrame = { type: 'ask'; id: string; text: string; source: InputSource }
export type InterruptFrame = { type: 'interrupt' }
export type ReplyFrame = { type: 'reply'; id: string } & Record<string, unknown>
/** Pestañas del informe: actualizar una fuente, pedir estado o auditoría. Solo lectura. */
export type BriefingRequestFrame =
  | { type: 'briefing'; action: 'refresh'; source: string }
  | { type: 'briefing'; action: 'status' }
  | { type: 'briefing'; action: 'audit' }
/** Pedir que se evalúe una acción propuesta en el informe (política + aprobación). */
export type ProposeFrame = { type: 'propose'; proposal: string }

export type ClientFrame =
  | HelloFrame
  | AskFrame
  | InterruptFrame
  | ReplyFrame
  | BriefingRequestFrame
  | ProposeFrame

// -- contratos del OpenClawAdapter (crisvis.openclaw.contracts) ---------------

export type Risk = 'READ_ONLY' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
export type AlertLevel = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'INFO'
export type SourceState =
  | 'success'
  | 'partial'
  | 'timeout'
  | 'unauthorized'
  | 'unavailable'
  | 'error'
  | 'cancelled'
export type ConnectorMode = 'no_configurado' | 'mock' | 'sandbox' | 'real'
export type Detail = 'quick' | 'standard' | 'complete'
export type BriefingTab =
  | 'resumen'
  | 'correo'
  | 'agenda'
  | 'tareas'
  | 'proyectos'
  | 'negocio'
  | 'documentos'
  | 'sistema'
  | 'automatizaciones'
  | 'integraciones'

export type BriefingItem = {
  id: string
  title: string
  subtitle: string
  when: string | null
  meta: Record<string, string | number | boolean>
  url: string
}
export type BriefingAlert = {
  id: string
  level: AlertLevel
  source: string
  title: string
  detail: string
}
export type ProposedAction = {
  id: string
  tool: string
  source: string
  risk: Risk
  service: string
  account: string
  target: string
  params: Record<string, unknown>
  preview: string
  impact: string
}
export type ConnectorResult = {
  source: string
  name: string
  tab: BriefingTab
  mode: ConnectorMode
  state: SourceState
  fetched_at: string
  duration_ms: number
  items: BriefingItem[]
  alerts: BriefingAlert[]
  proposals: ProposedAction[]
  counts: Record<string, number>
  message: string
}
export type ConnectorStatus = {
  id: string
  name: string
  tab: BriefingTab
  mode: ConnectorMode
  access: 'read_only' | 'draft' | 'write' | 'delete'
  integration: string
  state: SourceState | null
  message: string
  checked_at: string | null
  approval: string
}
export type DailyBriefingRequest = {
  user: string
  timezone: string
  date: string
  detail: Detail
  sources: string[]
  timeout_s: number
  source_timeout_s: number
  dry_run: boolean
  trigger: string
}
export type DailyBriefingResult = {
  id: string
  request: DailyBriefingRequest
  started_at: string
  finished_at: string | null
  results: ConnectorResult[]
  alerts: BriefingAlert[]
  voice_summary: string
  dry_run: boolean
  cancelled: boolean
}
export type ApprovalRequest = {
  id: string
  action: ProposedAction
  fingerprint: string
  created_at: string
  expires_at: string
  state: 'pending' | 'approved' | 'denied' | 'expired' | 'used'
  dry_run: boolean
}
export type AdapterOverview = {
  enabled: boolean
  dryRun: boolean
  policy: Record<Risk, string>
  gateway: Record<string, unknown> & { state: string; message: string }
  connectors: ConnectorStatus[]
  pending: ApprovalRequest[]
  workflow: { id: string; version: number }
}

// -- núcleo -> cara ----------------------------------------------------------

export type ReadyFrame = {
  type: 'ready'
  session: string
  servers: string[]
  brain: BrainStatus
  permissions: PermissionStatus
  resumed: boolean
}
export type StatusFrame = {
  type: 'status'
  servers: string[]
  brain: BrainStatus
  permissions: PermissionStatus
}
export type TextFrame = { type: 'text'; delta: string; ask: string | null }
export type ToolFrame = { type: 'tool'; name: string; ask: string | null }
export type DoneFrame = { type: 'done'; text: string; ask: string | null }
export type ErrorFrame = { type: 'error'; message: string; ask?: string | null }
export type PanelFrame = { type: 'panel'; panel: Record<string, unknown> }
export type BladeFrame = { type: 'blade'; blade: Record<string, unknown> }
export type UiFrame = { type: 'ui'; op: string; args: Record<string, unknown> }
export type CaptureFrame = {
  type: 'capture'
  id: string
  mode: 'look' | 'watch'
  reason: string
  seconds: number
  when: 'now' | 'past'
}
export type ConfirmFrame = {
  type: 'confirm'
  id: string
  tool: string
  tier: 'interfaz' | 'lectura' | 'escritura' | 'peligroso'
  risk: ActionRisk
  summary: string
  /** Aviso extra (datos sensibles, no se puede deshacer…). */
  warning: string
  /** false = una vez iniciada no se puede cancelar. */
  cancellable: boolean
  /** Ventana sobre la que actuará (control del PC). */
  target: string
  timeout: number
  seconds: number
  ask: string | null
  /** Aprobación de un solo uso: hay que devolver ambos tal cual. */
  grant: string
  nonce: string
}

/** Respuesta de la cara a una trama `confirm`. Vale para esa acción y nada más. */
export type ConfirmReply = {
  type: 'reply'
  id: string
  approved: boolean
  grant: string
  nonce: string
}

/** Stop sobre una herramienta en marcha. */
export type CancelFrame = {
  type: 'cancel'
  phase: 'requested' | 'cancelled' | 'failed'
  exec: string
  tool: string
  cancellable: boolean
  message: string
}

/** El núcleo aceptó (o no) una entrada; solo entonces se muestra como del usuario. */
export type InputFrame = {
  type: 'input'
  ask: string | null
  event: string
  accepted: boolean
  message?: string
}

export type BriefingFrame =
  | {
      type: 'briefing'
      phase: 'start'
      request: DailyBriefingRequest
      tabs: Record<BriefingTab, string[]>
      connectors: ConnectorStatus[]
    }
  | { type: 'briefing'; phase: 'source'; result: ConnectorResult }
  | { type: 'briefing'; phase: 'done'; result: DailyBriefingResult }
  | { type: 'briefing'; phase: 'cancelled' }
  | { type: 'briefing'; phase: 'error'; message: string; source?: string }
  | { type: 'briefing'; phase: 'status'; overview: AdapterOverview }
  | { type: 'briefing'; phase: 'audit'; events: Record<string, unknown>[] }

/** Una acción HIGH/MEDIUM/CRITICAL pendiente de aprobación exacta. */
export type ApprovalFrame = { type: 'approval'; id: string; request: ApprovalRequest }
/** La respuesta vale solo si trae la huella exacta de la acción mostrada. */
export type ApprovalReply = { type: 'reply'; id: string; approved: boolean; fingerprint: string }

export type OutcomeFrame = {
  type: 'outcome'
  proposal: string
  status: 'blocked' | 'needs_approval' | 'denied' | 'simulated' | 'done' | 'error'
  message: string
  risk?: Risk
  executed?: boolean
  simulated?: boolean
}

export type CoreFrame =
  | ReadyFrame
  | StatusFrame
  | TextFrame
  | ToolFrame
  | DoneFrame
  | ErrorFrame
  | PanelFrame
  | BladeFrame
  | UiFrame
  | CaptureFrame
  | ConfirmFrame
  | BriefingFrame
  | ApprovalFrame
  | OutcomeFrame
  | CancelFrame
  | InputFrame

export const CLIENT_FRAMES = [
  'hello',
  'ask',
  'interrupt',
  'reply',
  'briefing',
  'propose',
] as const satisfies readonly ClientFrame['type'][]

export const CORE_FRAMES = [
  'ready',
  'status',
  'text',
  'tool',
  'done',
  'error',
  'panel',
  'blade',
  'ui',
  'capture',
  'confirm',
  'briefing',
  'approval',
  'outcome',
  'cancel',
  'input',
] as const satisfies readonly CoreFrame['type'][]
