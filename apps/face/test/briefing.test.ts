// Pruebas de la lógica de pestañas del informe. `npm test -w @crisvis/face`.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { BriefingFrame, ConnectorResult, DailyBriefingRequest } from '@crisvis/protocol'
import {
  TABS,
  applyFrame,
  alertsFor,
  clock,
  emptyText,
  emptyView,
  failureText,
  isMock,
  lastUpdate,
  resultsFor,
  sortAlerts,
  summaryCounts,
  tabState,
} from '../src/lib/briefing.ts'

const request: DailyBriefingRequest = {
  user: 'Cristofer',
  timezone: 'America/Lima',
  date: '2026-10-03',
  detail: 'standard',
  sources: [],
  timeout_s: 20,
  source_timeout_s: 6,
  dry_run: true,
  trigger: 'Buenos días',
}

function result(source: string, state: ConnectorResult['state'], extra: Partial<ConnectorResult> = {}) {
  return {
    source,
    name: source,
    tab: 'correo',
    mode: 'mock',
    state,
    fetched_at: '2026-10-03T13:40:00Z',
    duration_ms: 10,
    items: [],
    alerts: [],
    proposals: [],
    counts: {},
    message: '',
    ...extra,
  } as ConnectorResult
}

function started() {
  return applyFrame(emptyView(), {
    type: 'briefing',
    phase: 'start',
    request,
    tabs: emptyView().tabs,
    connectors: [],
  })
}

test('hay diez pestañas en el orden pedido', () => {
  assert.deepEqual(
    TABS.map((t) => t.label),
    [
      'Resumen',
      'Correo',
      'Agenda',
      'Tareas',
      'Proyectos',
      'Negocio',
      'Documentos',
      'Sistema',
      'Automatizaciones',
      'Integraciones',
    ],
  )
})

test('al empezar, todas las pestañas están consultando', () => {
  const view = started()
  assert.equal(view.open, true)
  assert.equal(view.running, true)
  for (const { id } of TABS) assert.equal(tabState(view, id), 'loading')
})

test('cada fuente actualiza solo su pestaña', () => {
  let view = started()
  view = applyFrame(view, { type: 'briefing', phase: 'source', result: result('correo', 'success') })
  assert.equal(tabState(view, 'correo'), 'success')
  assert.equal(tabState(view, 'agenda'), 'loading')
})

test('una pestaña con una fuente caída y otra bien es parcial', () => {
  let view = started()
  view = applyFrame(view, { type: 'briefing', phase: 'source', result: result('github', 'success') })
  view = applyFrame(view, { type: 'briefing', phase: 'source', result: result('gitlab', 'timeout') })
  assert.equal(tabState(view, 'proyectos'), 'partial')
})

test('si fallan todas, se ve el fallo y un mensaje seguro', () => {
  let view = started()
  const r = result('n8n', 'timeout', { message: 'No respondió en 6 s.' })
  view = applyFrame(view, { type: 'briefing', phase: 'source', result: r })
  assert.equal(tabState(view, 'automatizaciones'), 'timeout')
  assert.match(failureText(r), /no respondió a tiempo/)
  assert.equal(failureText(result('crm', 'unauthorized')).includes('Integraciones'), true)
  assert.equal(failureText(result('x', 'success')), '')
})

test('las alertas se ordenan por gravedad y se filtran por pestaña', () => {
  const alerts = [
    { id: '1', level: 'INFO', source: 'agenda', title: 'a', detail: '' },
    { id: '2', level: 'CRITICAL', source: 'sistema', title: 'b', detail: '' },
    { id: '3', level: 'HIGH', source: 'correo', title: 'c', detail: '' },
  ] as const
  const sorted = sortAlerts([...alerts])
  assert.deepEqual(
    sorted.map((a) => a.level),
    ['CRITICAL', 'HIGH', 'INFO'],
  )
  let view = started()
  view = applyFrame(view, {
    type: 'briefing',
    phase: 'source',
    result: result('correo', 'success', { alerts: [alerts[2]] }),
  })
  assert.equal(alertsFor(view, 'correo').length, 1)
  assert.equal(alertsFor(view, 'agenda').length, 0)
  assert.equal(alertsFor(view, 'resumen').length, 1)
})

test('el informe completo cierra la carga y guarda el resumen de voz', () => {
  const done: BriefingFrame = {
    type: 'briefing',
    phase: 'done',
    result: {
      id: 'inf-1',
      request,
      started_at: '2026-10-03T13:40:00Z',
      finished_at: '2026-10-03T13:40:02Z',
      results: [result('correo', 'success', { counts: { prioritarios: 3 } })],
      alerts: [],
      voice_summary: 'Buenos días, Cristofer.',
      dry_run: true,
      cancelled: false,
    },
  }
  const view = applyFrame(started(), done)
  assert.equal(view.running, false)
  assert.equal(view.loading.length, 0)
  assert.equal(view.voice, 'Buenos días, Cristofer.')
  assert.equal(resultsFor(view, 'correo').length, 1)
  assert.equal(isMock(view), true)
  const counts = summaryCounts(view)
  assert.equal(counts[0].value, 3)
  // Sin datos de la agenda, no se inventa un cero.
  assert.equal(counts[1].value, null)
})

test('cancelar (Stop) deja de consultar', () => {
  const view = applyFrame(started(), { type: 'briefing', phase: 'cancelled' })
  assert.equal(view.running, false)
  assert.equal(view.loading.length, 0)
  assert.equal(tabState(view, 'correo'), 'idle')
})

test('un error al refrescar una fuente no cancela el resto', () => {
  const view = applyFrame(started(), {
    type: 'briefing',
    phase: 'error',
    source: 'agenda',
    message: 'Espera un momento antes de volver a actualizar.',
  })
  assert.equal(view.running, true)
  assert.equal(view.loading.includes('agenda'), false)
  assert.equal(view.loading.includes('correo'), true)
})

test('vista vacía útil en cada pestaña', () => {
  for (const { id } of TABS) assert.ok(emptyText(id).length > 10)
  assert.equal(tabState(emptyView(), 'correo'), 'idle')
  assert.equal(lastUpdate(emptyView(), 'correo'), null)
})

test('la hora se muestra en la zona de Lima', () => {
  assert.equal(clock('2026-10-03T13:40:00Z'), '08:40')
  assert.equal(clock(null), '—')
  assert.equal(clock('no es fecha'), '—')
})
