/**
 * Contrato del WebSocket entre la cara (apps/face) y el núcleo (src/crisvis).
 *
 * Los nombres de trama se comprueban contra ../frames.json (npm test), igual
 * que el lado Python (tests/test_protocol.py). Cambiar una trama aquí sin
 * cambiarla allí rompe la comprobación, que es justo la idea.
 */

export const PROTOCOL_VERSION = 1

export type PermissionMode = 'lectura' | 'confirmar' | 'libre'

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
}

// -- cara -> núcleo ----------------------------------------------------------

export type HelloFrame = { type: 'hello'; session: string; protocol: number }
export type AskFrame = { type: 'ask'; id: string; text: string }
export type InterruptFrame = { type: 'interrupt' }
export type ReplyFrame = { type: 'reply'; id: string } & Record<string, unknown>
export type PermissionsFrame = { type: 'permissions'; mode: PermissionMode }

export type ClientFrame =
  | HelloFrame
  | AskFrame
  | InterruptFrame
  | ReplyFrame
  | PermissionsFrame

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
  summary: string
  seconds: number
  ask: string | null
}

/** Respuesta de la cara a una trama `confirm`. */
export type ConfirmReply = { type: 'reply'; id: string; approved: boolean }

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

export const CLIENT_FRAMES = [
  'hello',
  'ask',
  'interrupt',
  'reply',
  'permissions',
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
] as const satisfies readonly CoreFrame['type'][]
