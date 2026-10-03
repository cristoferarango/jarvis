import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useStore } from '../store'
import { answerConfirm } from '../lib/confirm'
import { voiceCanApprove } from '../lib/guardrails'

const RISK_LABEL = {
  low: 'RIESGO BAJO',
  medium: 'RIESGO MEDIO',
  high: 'RIESGO ALTO',
  critical: 'CRÍTICO',
} as const

/**
 * El cerebro pide permiso. Se contesta con los botones, con Intro / Escape, o
 * de viva voz ("sí" / "no") — App.tsx enruta la voz mientras esto está abierto.
 */
export function Confirm() {
  const confirm = useStore((s) => s.confirm)
  const [, tick] = useState(0)

  useEffect(() => {
    if (!confirm) return
    const timer = window.setInterval(() => tick((n) => n + 1), 250)
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      if (e.key === 'Enter') {
        e.preventDefault()
        e.stopImmediatePropagation()
        answerConfirm(true)
      } else if (e.key === 'Escape') {
        e.preventDefault()
        e.stopImmediatePropagation()
        answerConfirm(false)
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('keydown', onKey, true)
    }
  }, [confirm])

  const left = confirm ? Math.max(0, Math.ceil((confirm.deadline - Date.now()) / 1000)) : 0

  return (
    <AnimatePresence>
      {confirm && (
        <motion.div
          key={confirm.id}
          className={`confirm confirm-${confirm.tier}`}
          role="alertdialog"
          aria-label="Permiso solicitado"
          initial={{ opacity: 0, x: '-50%', y: 12, filter: 'blur(6px)' }}
          animate={{ opacity: 1, x: '-50%', y: 0, filter: 'blur(0px)' }}
          exit={{ opacity: 0, x: '-50%', y: 12, filter: 'blur(6px)' }}
          transition={{ type: 'spring', stiffness: 300, damping: 26 }}
        >
          <div className="confirm-head">
            <span className="confirm-kicker">PERMISO PARA ESTA ACCIÓN</span>
            <span className="confirm-tier">{RISK_LABEL[confirm.risk] ?? confirm.risk}</span>
            <span className="confirm-clock">{left}s</span>
          </div>
          <div className="confirm-summary">{confirm.summary}</div>
          {confirm.target && <div className="confirm-tool">Ventana: {confirm.target}</div>}
          {confirm.warning && <div className="confirm-warning">{confirm.warning}</div>}
          {!confirm.cancellable && confirm.risk !== 'low' && (
            <div className="confirm-warning">Una vez iniciada, esta acción no se puede cancelar.</div>
          )}
          <div className="confirm-tool">{confirm.tool}</div>
          <div className="confirm-actions">
            <button type="button" className="confirm-yes" onClick={() => answerConfirm(true)}>
              Permitir <kbd>Intro</kbd>
            </button>
            <button type="button" className="confirm-no" onClick={() => answerConfirm(false)}>
              Denegar <kbd>Esc</kbd>
            </button>
          </div>
          <div className="confirm-hint">
            {voiceCanApprove(confirm.risk)
              ? 'o diga «sí» / «no»'
              : 'Riesgo alto: apruebe con el botón o Intro'}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
