import { useEffect, useState, type FormEvent } from 'react'
import type { PermissionMode } from '@crisvis/protocol'
import { useStore } from '../store'
import { coreStatus, setPermissions } from '../lib/brain'
import { libreLeft, userSelectable } from '../lib/guardrails'

const MODES: Array<{ mode: PermissionMode; label: string; hint: string }> = [
  { mode: 'lectura', label: 'SOLO LECTURA', hint: 'Solo herramientas que no cambian nada.' },
  { mode: 'confirmar', label: 'CONFIRMAR', hint: 'Pide permiso para cada acción que cambie algo.' },
  {
    mode: 'libre',
    label: 'LIBRE',
    hint: 'Deshabilitado. Solo se activa unos minutos desde una terminal (crisvis permisos libre).',
  },
]

const LIBRE_HOWTO =
  'LIBRE está deshabilitado desde la interfaz.\n\n' +
  'Para habilitarlo unos minutos, en una terminal de este equipo:\n' +
  '  uv run --no-sync python -m crisvis permisos libre --minutos 10\n\n' +
  'Teclado, ratón, portapapeles, órdenes y acciones de sistema se siguen aprobando una a una. ' +
  'Caduca solo y vuelve a CONFIRMAR al reiniciar.'

/**
 * La consola: escribir en lugar de hablar, y bajar o restaurar los permisos.
 * El modo lo decide el núcleo; aquí solo se pide LECTURA o CONFIRMAR.
 */
export function Console({ onAsk }: { onAsk: (text: string) => void }) {
  const phase = useStore((s) => s.phase)
  const permissions = useStore((s) => s.permissions)
  const [text, setText] = useState('')
  const [, tick] = useState(0)

  const libreUntil = permissions === 'libre' ? coreStatus().libreUntil : null
  useEffect(() => {
    if (!libreUntil) return
    const t = window.setInterval(() => tick((n) => n + 1), 1000)
    return () => window.clearInterval(t)
  }, [libreUntil])

  if (phase === 'offline' || phase === 'boot') return null

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const said = text.trim()
    if (!said) return
    setText('')
    onAsk(said)
  }

  const choose = (mode: PermissionMode) => {
    if (mode === permissions) return
    if (!userSelectable(mode)) {
      window.alert(LIBRE_HOWTO)
      return
    }
    setPermissions(mode).catch((err: unknown) => {
      useStore.getState().setError(err instanceof Error ? err.message : String(err))
    })
  }

  return (
    <div className="console">
      <div className="console-modes" role="radiogroup" aria-label="Modo de permisos">
        {MODES.map((m) => (
          <button
            key={m.mode}
            type="button"
            role="radio"
            aria-checked={permissions === m.mode}
            title={m.hint}
            className={`console-mode console-mode-${m.mode}${permissions === m.mode ? ' on' : ''}`}
            onClick={() => choose(m.mode)}
          >
            {m.label}
            {m.mode === 'libre' && libreUntil ? ` · ${libreLeft(libreUntil, Date.now())}` : ''}
          </button>
        ))}
      </div>
      <form className="console-form" onSubmit={submit}>
        <input
          className="console-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') (e.target as HTMLInputElement).blur()
          }}
          placeholder="Escriba una orden…"
          maxLength={8000}
          aria-label="Orden escrita"
          spellCheck={false}
        />
      </form>
    </div>
  )
}
