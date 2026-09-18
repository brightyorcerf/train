import type { TraceResult } from '../api/client'

/** The crowned target, its one-line rationale, and the value that landed at the endpoint (Axis 3).
 *  Abstention gets the same visual weight as an answer — refusing to crown is the disciplined
 *  outcome, not an error state. */
export function RecommendedTarget({ r }: { r: TraceResult }) {
  const top = (r.vasp_candidates ?? []).find((c) => c.entity === r.recommended)
  const dep = r.nearest?.deposit_event ?? r.deposit_event
  const asset = dep?.amount_btc != null ? 'BTC' : (r.nearest?.path?.[0]?.asset ?? '')

  if (!r.recommended) {
    // INCOMPLETE is not abstention: the data never arrived. Calling a degraded trace an honest
    // non-answer claimed discipline for what was actually missing evidence.
    const incomplete = r.state === 'INCOMPLETE' || r.partial
    return (
      <div className="panel">
        <h2>Recommended target</h2>
        <div className={incomplete ? 'red' : 'amber'} style={{ fontSize: 18 }}>
          No target crowned — {r.state}
        </div>
        <div className="note">
          {r.rationale ?? r.reason ?? 'no labeled or sweep-provable endpoint reached'}.
          {incomplete
            ? ' This is NOT an abstention: part of the graph was never fetched (provider'
              + ' unavailable), so the result is incomplete rather than a considered non-answer.'
              + ' Re-run it before drawing any conclusion.'
            : r.flags?.length > 0
              ? ' The trace stopped at a boundary rather than guessing past it.'
              : ''}
        </div>
        {r.flags?.length > 0 && (
          <div className="row" style={{ marginTop: 10 }}>
            {r.flags.slice(0, 4).map((f) => (
              <span key={f} className="tag red mono" style={{ fontSize: 11 }}>{f.split('(')[0]}</span>
            ))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="panel crown">
      <h2>Recommended target</h2>
      <div className="row" style={{ alignItems: 'baseline' }}>
        <span className="gold" style={{ fontSize: 26, fontWeight: 700 }}>{top?.entity_name ?? r.recommended}</span>
        {top && <span><b style={{ fontSize: 18 }}>{top.score}</b><span className="dim"> / 100</span></span>}
        <span className={`tag ${top?.sahyog === 'confirmed' ? 'green' : 'amber'}`}>
          sahyog: {top?.sahyog ?? 'unknown'}
        </span>
      </div>
      <div className="note" style={{ fontSize: 13 }}>{r.rationale}</div>
      {dep && (
        <div className="row" style={{ marginTop: 12, gap: 24 }}>
          <span>
            <span className="dim">value at endpoint </span>
            <b className="cyan mono">{dep.amount_btc ?? dep.amount} {asset}</b>
          </span>
          <span className="dim mono" style={{ fontSize: 12 }}>deposit tx {dep.tx.slice(0, 16)}…</span>
        </div>
      )}
      <div className="note">
        Value is the amount that landed at the endpoint, not the suspect's own share — a deposit
        transaction can aggregate many senders. Investigative lead, not identity and not evidence.
      </div>
    </div>
  )
}
