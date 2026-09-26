import type { TraceResult } from '../api/client'

/** Ranked candidates + the separation indicator. Score is a confidence INDEX out of 100 (§11.1),
 *  never a probability — the header says so rather than leaving a bare number to be misread. */
export function Leaderboard({ r }: { r: TraceResult }) {
  const cands = r.vasp_candidates ?? []
  if (cands.length === 0) {
    return (
      <div className="panel">
        <h2>Leaderboard</h2>
        <div className="dim">No VASP candidate reached within the budget.</div>
        <div className="note">
          {r.reason ? `Terminated: ${r.reason}.` : ''} An empty leaderboard is a result, not a
          failure. Nothing is crowned on absence of evidence.
        </div>
      </div>
    )
  }
  return (
    <div className="panel">
      <h2>
        Leaderboard: confidence index /100
        {/* Separation is the gap between the top TWO. With one candidate the backend now sends
            null, and saying so beats printing the sole score as if it were a margin. */}
        {r.separation ? (
          <span className={`tag ${r.separation === 'HIGH' ? 'green' : 'amber'}`} style={{ marginLeft: 10 }}>
            separation: {r.separation}{r.separation_pts != null ? ` (${r.separation_pts} pts)` : ''}
          </span>
        ) : cands.length === 1 ? (
          <span className="tag dim" style={{ marginLeft: 10 }}>single candidate, no separation</span>
        ) : null}
      </h2>
      <table>
        <thead>
          <tr><th></th><th>entity</th><th>score</th><th></th><th>endpoint</th><th>sahyog</th><th>paths</th></tr>
        </thead>
        <tbody>
          {cands.map((c, i) => (
            <tr key={c.entity} className={c.entity === r.recommended ? 'sel' : ''}>
              <td className="mono dim">#{i + 1}</td>
              <td className={c.entity === r.recommended ? 'gold' : ''}>
                {c.entity_name}
                {c.entity === r.recommended && <span className="dim"> · crowned</span>}
              </td>
              <td><b>{c.score}</b><span className="dim"> / 100</span></td>
              <td style={{ width: 120 }}>
                <div className="bar"><span style={{ width: `${c.score}%` }} /></div>
              </td>
              <td className="mono trunc dim">{c.nearest?.endpoint?.slice(0, 12)}…</td>
              <td>
                <span className={`tag ${c.sahyog === 'confirmed' ? 'green' : 'amber'}`}>{c.sahyog}</span>
              </td>
              <td className="dim">{c.n_paths}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="note">
        A confidence index, not a probability. Below a 10-point gap between #1 and #2 the engine
        declines to crown either.
        {r.below_floor && ' This candidate is listed but NOT crowned: it scores below the floor for'
          + ' naming a disclosure target, so the engine reports it without recommending it.'}
      </div>
    </div>
  )
}
