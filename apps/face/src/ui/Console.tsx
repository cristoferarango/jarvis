import { useState, type FormEvent } from 'react'
import type { PermissionMode } from '@crisvis/protocol'
import { useStore } from '../store'
import { setPermissions } from '../lib/brain'

const MODES: Array<{ mode: PermissionMode; label: string; hint: string }> = [
  { mode: 'lectura', label: 'SOLO LECTURA', hint: 'Solo herramientas que no cambian nada.' },
  { mode: 'confirmar', label: 'CONFIRMAR', hint: 'Pide permiso antes de escribir o ejecutar.' },
  { mode: 'libre', label: 'LIBRE', hint: 'Actúa sin preguntar (salvo lo denegado en la configuración).' },
]

/**
 * La consola: escribir en lugar de hablar, y elegir cuánto puede hacer el
 * cerebro sin preguntar. Vive en la esquina inferior izquierda.
 */
export function Console({ onAsk }: { onAsk: (text: string) => void }) {
  const phase = useStore((s) => s.phase)
  const permissions = useStore((s) => s.permissions)
  const [text, setText] = useState('')

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
    if (
      mode === 'libre' &&
      !window.confirm(
        'Modo LIBRE: el asistente podrá escribir archivos y ejecutar órdenes sin pedir permiso. ¿Continuar?',
      )
    ) {
      return
    }
    setPermissions(mode)
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
