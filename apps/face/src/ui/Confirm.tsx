import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useStore } from '../store'
import { answerConfirm } from '../lib/confirm'

const TIER_LABEL = {
  interfaz: 'INTERFAZ',
  lectura: 'LECTURA',
  escritura: 'ESCRITURA',
  peligroso: 'PELIGROSO',
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
            <span className="confirm-kicker">PERMISO SOLICITADO</span>
            <span className="confirm-tier">{TIER_LABEL[confirm.tier] ?? confirm.tier}</span>
            <span className="confirm-clock">{left}s</span>
          </div>
          <div className="confirm-summary">{confirm.summary}</div>
          <div className="confirm-tool">{confirm.tool}</div>
          <div className="confirm-actions">
            <button type="button" className="confirm-yes" onClick={() => answerConfirm(true)}>
              Permitir <kbd>Intro</kbd>
            </button>
            <button type="button" className="confirm-no" onClick={() => answerConfirm(false)}>
              Denegar <kbd>Esc</kbd>
            </button>
          </div>
          <div className="confirm-hint">o diga «sí» / «no»</div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
