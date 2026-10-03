import { useEffect } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import type { BriefingItem, BriefingTab, ConnectorResult, ProposedAction } from '@crisvis/protocol'
import { useBriefing } from '../lib/briefingStore'
import { proposeAction, requestBriefing } from '../lib/brain'
import {
  MODE_LABEL,
  STATE_LABEL,
  TABS,
  alertsFor,
  clock,
  emptyText,
  failureText,
  isMock,
  lastUpdate,
  resultsFor,
  sourcesFor,
  summaryCounts,
  tabState,
  type BriefingView,
} from '../lib/briefing'

/**
 * El informe del día: un panel con diez pestañas. Lo abre el flujo
 * daily-briefing del núcleo (por voz o con el botón) y se rellena fuente a
 * fuente según van respondiendo.
 */
export function Briefing({ onRun }: { onRun: () => void }) {
  const view = useBriefing()
  const { open, tab, setOpen, setTab } = view

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      if (e.key === 'Escape' && !useBriefing.getState().approval) setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, setOpen])

  useEffect(() => {
    if (open && tab === 'integraciones') {
      requestBriefing('status')
      requestBriefing('audit')
    }
  }, [open, tab])

  const tz = view.request?.timezone ?? 'America/Lima'

  return (
    <>
      <button
        type="button"
        className={`briefing-launch${view.running ? ' is-running' : ''}`}
        onClick={() => (view.results && Object.keys(view.results).length ? setOpen(!open) : onRun())}
        title="Informe del día"
      >
        INFORME
      </button>
      <AnimatePresence>
        {open && (
          <motion.section
            className="briefing"
            role="dialog"
            aria-label="Informe del día"
            initial={{ opacity: 0, x: '-50%', y: 16 }}
            animate={{ opacity: 1, x: '-50%', y: 0 }}
            exit={{ opacity: 0, x: '-50%', y: 16 }}
            transition={{ type: 'spring', stiffness: 260, damping: 28 }}
          >
            <header className="briefing-head">
              <span className="briefing-title">INFORME DEL DÍA</span>
              <span className="briefing-meta">
                {view.request?.date ?? ''} · {view.request?.detail ?? 'standard'}
              </span>
              {view.request?.dry_run !== false && (
                <span className="briefing-badge badge-dry">DRY_RUN</span>
              )}
              {isMock(view) && <span className="briefing-badge badge-mock">DATOS DE PRUEBA</span>}
              <span className="briefing-spacer" />
              <button type="button" className="briefing-run" onClick={onRun} disabled={view.running}>
                {view.running ? 'Consultando…' : 'Nuevo informe'}
              </button>
              <button
                type="button"
                className="briefing-close"
                onClick={() => setOpen(false)}
                aria-label="Cerrar"
              >
                ✕
              </button>
            </header>
            <nav className="briefing-tabs" role="tablist">
              {TABS.map((t) => {
                const state = tabState(view, t.id)
                return (
                  <button
                    key={t.id}
                    type="button"
                    role="tab"
                    aria-selected={tab === t.id}
                    className={`briefing-tab state-${state}${tab === t.id ? ' is-active' : ''}`}
                    onClick={() => setTab(t.id)}
                  >
                    <i className="dot" />
                    {t.label}
                  </button>
                )
              })}
            </nav>
            <div className="briefing-body" role="tabpanel">
              <TabToolbar view={view} tab={tab} tz={tz} onRun={onRun} />
              {view.error && <div className="briefing-error">{view.error}</div>}
              {tab === 'resumen' ? (
                <Summary view={view} />
              ) : tab === 'integraciones' ? (
                <Integrations view={view} tz={tz} />
              ) : (
                <SourceTab view={view} tab={tab} tz={tz} />
              )}
            </div>
          </motion.section>
        )}
      </AnimatePresence>
    </>
  )
}

function TabToolbar({
  view,
  tab,
  tz,
  onRun,
}: {
  view: BriefingView
  tab: BriefingTab
  tz: string
  onRun: () => void
}) {
  const state = tabState(view, tab)
  const sources = sourcesFor(view, tab)
  const names = sources.map((s) => {
    const r = view.results[s]
    const c = view.connectors.find((x) => x.id === s)
    const mode = r?.mode ?? c?.mode
    return `${r?.name ?? c?.name ?? s}${mode ? ` (${MODE_LABEL[mode]})` : ''}`
  })
  const refresh = () => {
    if (tab === 'resumen') onRun()
    else if (tab === 'integraciones') {
      requestBriefing('status')
      requestBriefing('audit')
    } else {
      useBriefing.setState((s) => ({ loading: [...new Set([...s.loading, ...sources])] }))
      for (const s of sources) requestBriefing('refresh', s)
    }
  }
  return (
    <div className="briefing-toolbar">
      <span className={`briefing-state state-${state}`}>{STATE_LABEL[state]}</span>
      {names.length > 0 && <span className="briefing-sources">Fuente: {names.join(', ')}</span>}
      <span className="briefing-updated">Actualizado: {clock(lastUpdate(view, tab), tz)}</span>
      <button
        type="button"
        className="briefing-refresh"
        onClick={refresh}
        disabled={state === 'loading'}
      >
        Actualizar
      </button>
    </div>
  )
}

function Alerts({ view, tab }: { view: BriefingView; tab: BriefingTab }) {
  const alerts = alertsFor(view, tab)
  if (!alerts.length) return null
  return (
    <ul className="briefing-alerts">
      {alerts.map((a) => (
        <li key={a.id} className={`alert alert-${a.level.toLowerCase()}`}>
          <span className="alert-level">{a.level}</span>
          <span className="alert-title">{a.title}</span>
        </li>
      ))}
    </ul>
  )
}

function Summary({ view }: { view: BriefingView }) {
  const counts = summaryCounts(view)
  const results = Object.values(view.results)
  if (!results.length && !view.running) {
    return <p className="briefing-empty">{emptyText('resumen')}</p>
  }
  return (
    <>
      {view.voice && <p className="briefing-voice">{view.voice}</p>}
      <div className="briefing-counts">
        {counts.map((c) => (
          <div key={c.label} className="count">
            <b>{c.value ?? '—'}</b>
            <span>{c.label}</span>
          </div>
        ))}
      </div>
      <Alerts view={view} tab="resumen" />
      <div className="briefing-sourcegrid">
        {results.map((r) => (
          <span key={r.source} className={`chip state-${r.state}`}>
            {r.name} · {STATE_LABEL[r.state]}
          </span>
        ))}
        {view.loading.map((s) => (
          <span key={s} className="chip state-loading">
            {s} · {STATE_LABEL.loading}
          </span>
        ))}
      </div>
    </>
  )
}

function SourceTab({ view, tab, tz }: { view: BriefingView; tab: BriefingTab; tz: string }) {
  const results = resultsFor(view, tab)
  const state = tabState(view, tab)
  if (state === 'loading' && !results.length) {
    return <p className="briefing-empty">Consultando…</p>
  }
  if (!results.length) return <p className="briefing-empty">{emptyText(tab)}</p>
  return (
    <>
      <Alerts view={view} tab={tab} />
      {results.map((r) => (
        <SourceBlock key={r.source} result={r} tab={tab} tz={tz} view={view} />
      ))}
    </>
  )
}

function SourceBlock({
  result,
  tab,
  tz,
  view,
}: {
  result: ConnectorResult
  tab: BriefingTab
  tz: string
  view: BriefingView
}) {
  const failed = failureText(result)
  return (
    <div className={`briefing-source state-${result.state}`}>
      {sourcesFor(view, tab).length > 1 && (
        <h4>
          {result.name} <small>{MODE_LABEL[result.mode]}</small>
        </h4>
      )}
      {failed ? (
        <p className="briefing-failure">{failed}</p>
      ) : (
        <>
          {result.state === 'partial' && <p className="briefing-partial">{result.message}</p>}
          {result.items.length ? (
            <ul className="briefing-items">
              {result.items.map((item) => (
                <Item key={item.id} item={item} tz={tz} />
              ))}
            </ul>
          ) : (
            <p className="briefing-empty">{emptyText(tab)}</p>
          )}
          {result.proposals.length > 0 && (
            <div className="briefing-proposals">
              <span className="proposals-title">Acciones propuestas (no se ejecuta nada sin tu aprobación)</span>
              {result.proposals.map((p) => (
                <Proposal key={p.id} proposal={p} view={view} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

const META_HIDDEN = new Set(['minutos_para', 'metrica'])

function Item({ item, tz }: { item: BriefingItem; tz: string }) {
  const chips = Object.entries(item.meta).filter(([k]) => !META_HIDDEN.has(k))
  return (
    <li className="briefing-item">
      {item.when && <time>{clock(item.when, tz)}</time>}
      <div>
        <div className="item-title">{item.title}</div>
        {item.subtitle && <div className="item-sub">{item.subtitle}</div>}
        {chips.length > 0 && (
          <div className="item-meta">
            {chips.map(([k, v]) => (
              <span key={k}>
                {k.replace(/_/g, ' ')}: {String(v)}
              </span>
            ))}
          </div>
        )}
      </div>
    </li>
  )
}

const RISK_LABEL = {
  READ_ONLY: 'lectura',
  LOW: 'bajo',
  MEDIUM: 'medio',
  HIGH: 'alto',
  CRITICAL: 'crítico',
} as const

function Proposal({ proposal, view }: { proposal: ProposedAction; view: BriefingView }) {
  const outcome = view.outcomes[proposal.id]
  return (
    <div className={`proposal risk-${proposal.risk.toLowerCase()}`}>
      <span className="proposal-risk">{RISK_LABEL[proposal.risk]}</span>
      <span className="proposal-text">
        {proposal.preview || proposal.tool} <small>→ {proposal.target}</small>
      </span>
      <button type="button" onClick={() => proposeAction(proposal.id)}>
        Solicitar
      </button>
      {outcome && <span className={`proposal-outcome out-${outcome.status}`}>{outcome.message}</span>}
    </div>
  )
}

function Integrations({ view, tz }: { view: BriefingView; tz: string }) {
  const o = view.overview
  const gw = o?.gateway
  return (
    <>
      <div className="briefing-gateway">
        <h4>OpenClaw Gateway</h4>
        {gw ? (
          <p>
            <b className={`gw-${gw.state}`}>{gw.state}</b> · {gw.message}
            {typeof gw.url === 'string' && <code> {gw.url}</code>}
          </p>
        ) : (
          <p className="briefing-empty">Consultando…</p>
        )}
        {o && (
          <p className="briefing-dryline">
            Modo simulación (DRY_RUN): <b>{o.dryRun ? 'activo' : 'desactivado'}</b>
          </p>
        )}
      </div>
      {o && (
        <table className="briefing-table">
          <thead>
            <tr>
              <th>Nivel</th>
              <th>Política</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(o.policy).map(([level, rule]) => (
              <tr key={level}>
                <td>{level}</td>
                <td>{rule}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <table className="briefing-table">
        <thead>
          <tr>
            <th>Conector</th>
            <th>Modo</th>
            <th>Integración</th>
            <th>Acceso</th>
            <th>Estado</th>
            <th>Comprobado</th>
          </tr>
        </thead>
        <tbody>
          {view.connectors.length ? (
            view.connectors.map((c) => (
              <tr key={c.id}>
                <td>{c.name}</td>
                <td>{MODE_LABEL[c.mode]}</td>
                <td>{c.integration}</td>
                <td>{c.access}</td>
                <td className={c.state ? `state-${c.state}` : ''}>
                  {c.state ? STATE_LABEL[c.state] : '—'}
                </td>
                <td>{clock(c.checked_at, tz)}</td>
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={6}>{emptyText('integraciones')}</td>
            </tr>
          )}
        </tbody>
      </table>
      {view.audit.length > 0 && (
        <div className="briefing-audit">
          <h4>Auditoría reciente</h4>
          <ul>
            {view.audit.slice(0, 20).map((e, i) => (
              <li key={i}>
                <time>{clock(String(e.ts ?? ''), tz)}</time> {String(e.kind ?? '')}{' '}
                {String(e.source || e.tool || '')} · {String(e.decision ?? '')}{' '}
                {String(e.outcome ?? '')}
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  )
}
