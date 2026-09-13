import type { TraceResult } from '../api/client'

/** The crowned target, its one-line rationale, and the value that landed at the endpoint (Axis 3).
 *  Abstention gets the same visual weight as an answer — refusing to crown is the disciplined
 *  outcome, not an error state. */
export function RecommendedTarget({ r }: { r: TraceResult }) {
  const top = (r.vasp_candidates ?? []).find((c) => c.entity === r.recommended)
  const dep = r.nearest?.deposit_event ?? r.deposit_event

  if (!r.recommended) {
    return (
      <div className="panel">
        <h2>Recommended target</h2>
        <div className="amber" style={{ fontSize: 18 }}>No target crowned — {r.state}</div>
        <div className="note">
          {r.rationale ?? r.reason ?? 'no labeled or sweep-provable endpoint reached'}.
          {r.flags?.length > 0 && ' The trace stopped at a boundary rather than guessing past it.'}
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
        <span className={`tag ${r.entity_sahyog === 'confirmed' ? 'green' : 'amber'}`}>
          sahyog: {r.entity_sahyog ?? top?.sahyog ?? 'unknown'}
        </span>
      </div>
      <div className="note" style={{ fontSize: 13 }}>{r.rationale}</div>
      {dep && (
        <div className="row" style={{ marginTop: 12, gap: 24 }}>
          <span>
            <span className="dim">value at endpoint </span>
            <b className="cyan mono">{dep.amount_btc ?? dep.amount} BTC</b>
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
