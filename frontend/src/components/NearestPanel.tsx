import type { TraceResult } from '../api/client'

const ts = (t: number) => new Date(t * 1000).toISOString().replace('T', ' ').slice(0, 19)

/** Axis 1: proximity. Deliberately a SEPARATE panel from the leaderboard — hops and confidence are
 *  two different claims (§3), and collapsing them into one number is the thing this project refuses. */
export function NearestPanel({ r }: { r: TraceResult }) {
  const n = r.nearest
  return (
    <div className="panel">
      <h2>Nearest endpoint — proximity, not confidence</h2>
      {!n ? (
        <div className="dim">No endpoint reached.</div>
      ) : (
        <>
          <div className="row" style={{ gap: 24 }}>
            <span><span className="dim">hops </span><b className="cyan" style={{ fontSize: 20 }}>{n.hops}</b></span>
            <span><span className="dim">role basis </span>
              <span className={`tag ${n.role_basis === 'labeled' ? 'green' : 'amber'}`}>{n.role_basis}</span>
            </span>
          </div>
          <div style={{ marginTop: 10 }}>
            <div className="dim" style={{ fontSize: 12 }}>endpoint</div>
            <div className="mono trunc cyan">{n.endpoint}</div>
          </div>
          {n.deposit_event && (
            <div style={{ marginTop: 10 }}>
              <div className="dim" style={{ fontSize: 12 }}>deposit event</div>
              <div className="mono trunc">{n.deposit_event.tx}</div>
              <div className="dim mono" style={{ fontSize: 12 }}>
                {ts(n.deposit_event.ts)} · {n.deposit_event.amount_btc ?? n.deposit_event.amount} BTC
              </div>
            </div>
          )}
          <div style={{ marginTop: 12 }}>
            <div className="dim" style={{ fontSize: 12, marginBottom: 4 }}>path ({n.path.length} hops)</div>
            <table>
              <thead><tr><th>#</th><th>from → to</th><th>value</th><th>tx</th></tr></thead>
              <tbody>
                {n.path.map((h, i) => (
                  <tr key={h.tx + i}>
                    <td className="dim">{i + 1}</td>
                    <td className="mono trunc" style={{ fontSize: 12 }}>
                      {h.from.slice(0, 10)}… → {h.to.slice(0, 10)}…
                    </td>
                    <td className="mono">{h.value} {h.asset}</td>
                    <td className="mono dim" style={{ fontSize: 12 }}>{h.tx.slice(0, 12)}…</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="note">
            Fewest hops ≠ highest confidence. This panel answers "how close", the leaderboard
            answers "how well evidenced" — they can disagree, and that disagreement is informative.
          </div>
        </>
      )}
    </div>
  )
}
