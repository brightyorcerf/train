import { useEffect } from 'react'
import type { CaseRow } from '../api/client'
import { CaseList } from './CaseList'
import { ConvergencePanel } from './ConvergencePanel'

/** Everything that is about MANY traces, not this report: the history, and cross-case convergence
 *  (§8). It lived inline above the report and read as part of it; it is a drawer now. */
export function RecentDrawer({ cases, selected, onSelect, onClose }: {
  cases: CaseRow[]
  selected: string | null
  onSelect: (id: string) => void
  onClose: () => void
}) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="recent traces">
        <header>
          <h2>Recent traces</h2>
          <button className="ghost" onClick={onClose} aria-label="close">Close · esc</button>
        </header>
        <CaseList rows={cases} selected={selected} onSelect={onSelect} />
        <ConvergencePanel cases={cases} />
      </aside>
    </>
  )
}
