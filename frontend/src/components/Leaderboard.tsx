import type { TraceResult } from '../api/client'

const MEDAL = ['🥇', '🥈', '🥉']

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
          failure — nothing is crowned on absence of evidence.
        </div>
      </div>
    )
  }
  return (
    <div className="panel">
      <h2>
        Leaderboard — confidence index /100
        {r.separation && (
          <span className={`tag ${r.separation === 'HIGH' ? 'green' : 'amber'}`} style={{ marginLeft: 10 }}>
            separation: {r.separation}{r.separation_pts != null ? ` (${r.separation_pts} pts)` : ''}
          </span>
        )}
      </h2>
      <table>
        <thead>
          <tr><th></th><th>entity</th><th>score</th><th></th><th>endpoint</th><th>sahyog</th><th>paths</th></tr>
        </thead>
        <tbody>
          {cands.map((c, i) => (
            <tr key={c.entity} className={c.entity === r.recommended ? 'sel' : ''}>
              <td>{MEDAL[i] ?? i + 1}</td>
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
        Confidence index, not a probability and not ownership. Separation is the gap between #1 and
        #2 — a LOW gap is why the engine sometimes declines to crown anything.
      </div>
    </div>
  )
}
