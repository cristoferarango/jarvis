import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useBriefing } from '../lib/briefingStore'

/**
 * Aprobación exacta de una acción MEDIUM/HIGH/CRITICAL propuesta en el informe.
 * Enseña todo lo que se va a hacer, tal cual. Solo se aprueba con el botón:
 * ni Intro ni la voz, para que nadie apruebe por accidente. Esc deniega.
 */
export function Approval() {
  const approval = useBriefing((s) => s.approval)
  const answer = useBriefing((s) => s.answerApproval)
  const [, tick] = useState(0)

  useEffect(() => {
    if (!approval) return
    const timer = window.setInterval(() => tick((n) => n + 1), 500)
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopImmediatePropagation()
        answer(false)
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('keydown', onKey, true)
    }
  }, [approval, answer])

  const req = approval?.request
  const left = req
    ? Math.max(0, Math.ceil((new Date(req.expires_at).getTime() - Date.now()) / 1000))
    : 0

  return (
    <AnimatePresence>
      {req && (
        <motion.div
          key={req.id}
          className={`approval approval-${req.action.risk.toLowerCase()}`}
          role="alertdialog"
          aria-label="Aprobación solicitada"
          initial={{ opacity: 0, x: '-50%', y: 12 }}
          animate={{ opacity: 1, x: '-50%', y: 0 }}
          exit={{ opacity: 0, x: '-50%', y: 12 }}
          transition={{ type: 'spring', stiffness: 300, damping: 26 }}
        >
          <div className="approval-head">
            <span className="approval-kicker">APROBACIÓN EXACTA</span>
            <span className="approval-risk">{req.action.risk}</span>
            <span className="approval-clock">{left}s</span>
          </div>
          {req.dry_run && (
            <div className="approval-dry">
              Modo simulación activo: aunque apruebes, no se hará nada fuera de CRISVIS.
            </div>
          )}
          <dl className="approval-grid">
            <dt>Servicio</dt>
            <dd>
              {req.action.service}
              {req.action.account ? ` · ${req.action.account}` : ''}
            </dd>
            <dt>Acción</dt>
            <dd>
              <code>{req.action.tool}</code>
            </dd>
            <dt>Destino</dt>
            <dd>{req.action.target}</dd>
            <dt>Parámetros</dt>
            <dd>
              <pre>{JSON.stringify(req.action.params, null, 2)}</pre>
            </dd>
            {req.action.preview && (
              <>
                <dt>Contenido</dt>
                <dd>
                  <pre>{req.action.preview}</pre>
                </dd>
              </>
            )}
            <dt>Impacto</dt>
            <dd>{req.action.impact || '—'}</dd>
            <dt>Huella</dt>
            <dd>
              <code>{req.fingerprint.slice(0, 16)}…</code> · vale solo para esta acción, una vez
            </dd>
          </dl>
          <div className="approval-actions">
            <button type="button" className="approval-yes" onClick={() => answer(true)}>
              Aprobar
            </button>
            <button type="button" className="approval-no" onClick={() => answer(false)}>
              Denegar <kbd>Esc</kbd>
            </button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
