import { create } from 'zustand'
import type { ApprovalRequest, BriefingTab, OutcomeFrame } from '@crisvis/protocol'
import { applyFrame, emptyView, type BriefingView } from './briefing'
import { watchApproval, watchBriefing, watchOutcome } from './brain'

type PendingApproval = { request: ApprovalRequest; resolve: (ok: boolean) => void }

type BriefingState = BriefingView & {
  approval: PendingApproval | null
  setOpen: (open: boolean) => void
  setTab: (tab: BriefingTab) => void
  answerApproval: (approved: boolean) => void
}

export const useBriefing = create<BriefingState>((set, get) => ({
  ...emptyView(),
  approval: null,
  setOpen: (open) => set({ open }),
  setTab: (tab) => set({ tab }),
  answerApproval: (approved) => {
    const pending = get().approval
    if (!pending) return
    set({ approval: null })
    pending.resolve(approved)
  },
}))

watchBriefing((frame) => {
  useBriefing.setState((s) => applyFrame(s, frame))
})

watchOutcome((frame: OutcomeFrame) => {
  useBriefing.setState((s) => ({ outcomes: { ...s.outcomes, [frame.proposal]: frame } }))
})

watchApproval(
  (request) =>
    new Promise<boolean>((resolve) => {
      // Una aprobación nueva sustituye a otra sin contestar: esa se deniega.
      useBriefing.getState().approval?.resolve(false)
      const left = new Date(request.expires_at).getTime() - Date.now()
      const timer = window.setTimeout(
        () => {
          if (useBriefing.getState().approval?.request.id === request.id) {
            useBriefing.getState().answerApproval(false)
          }
        },
        Math.max(1000, left),
      )
      useBriefing.setState({
        approval: {
          request,
          resolve: (ok) => {
            window.clearTimeout(timer)
            resolve(ok)
          },
        },
      })
    }),
)
