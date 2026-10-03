// Reglas de seguridad de la cara (Fase 3.5). `npm test -w @crisvis/face`.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  cancelNotice,
  isNoiseUtterance,
  libreLeft,
  makeListenWindow,
  makeRepeatGuard,
  tokenFresh,
  userSelectable,
  voiceCanApprove,
} from '../src/lib/guardrails.ts'

test('un «sí» de viva voz no aprueba acciones de riesgo alto', () => {
  assert.equal(voiceCanApprove('medium'), true)
  assert.equal(voiceCanApprove('low'), true)
  assert.equal(voiceCanApprove('high'), false)
  assert.equal(voiceCanApprove('critical'), false)
})

test('la cara solo puede pedir LECTURA o CONFIRMAR, nunca LIBRE', () => {
  assert.equal(userSelectable('lectura'), true)
  assert.equal(userSelectable('confirmar'), true)
  assert.equal(userSelectable('libre'), false)
})

test('la cuenta atrás de LIBRE', () => {
  assert.equal(libreLeft(null, 0), '')
  assert.equal(libreLeft(1_000 + 125, 1_000_000), '2:05')
  assert.equal(libreLeft(1_000, 2_000_000), '0:00')
})

test('Stop nunca se presenta como completado', () => {
  assert.deepEqual(cancelNotice({ phase: 'requested', tool: 'shell_exec', message: '' }), {
    kind: 'caption',
    text: 'Cancelando…',
  })
  assert.deepEqual(cancelNotice({ phase: 'cancelled', tool: 'shell_exec', message: '' }), {
    kind: 'caption',
    text: 'Cancelado.',
  })
  const failed = cancelNotice({ phase: 'failed', tool: 'pc_type', message: '' })
  assert.equal(failed.kind, 'error')
  assert.match(failed.text, /No se pudo cancelar pc_type/)
})

test('una palabra funcional suelta oída por el micrófono es ruido, no una orden', () => {
  for (const noise of ['como', 'Cómo.', '¿qué?', 'eh', 'y', '  ', '...']) {
    assert.equal(isNoiseUtterance(noise), true, noise)
  }
  for (const real of ['sí', 'no', 'para', 'hora', 'como estás', 'informe del día', 'qué hora es']) {
    assert.equal(isNoiseUtterance(real), false, real)
  }
})

test('la misma frase dos veces seguidas no corta la respuesta', () => {
  const repeat = makeRepeatGuard(6000)
  assert.equal(repeat('Qué hora es', 1_000), false)
  assert.equal(repeat('qué hora es.', 3_300), true)
  assert.equal(repeat('qué hora es', 20_000), false)
  assert.equal(repeat('y mañana', 21_000), false)
})

test('el audio de fondo no alarga la ventana de escucha', () => {
  const win = makeListenWindow()
  assert.equal(win.remaining(0), 0)
  win.open(14_000, 1_000)
  assert.equal(win.remaining(5_000), 10_000)
  // Comprobar lo que queda no la reabre: solo open() (decir su nombre) lo hace.
  assert.equal(win.remaining(14_000), 1_000)
  assert.equal(win.remaining(20_000), 0)
  win.open(14_000, 20_000)
  assert.equal(win.remaining(20_000), 14_000)
})

test('el token se renueva antes de caducar', () => {
  assert.equal(tokenFresh(200_000, 100_000), true)
  assert.equal(tokenFresh(150_000, 100_000), false)
  assert.equal(tokenFresh(100_000, 100_000), false)
})
