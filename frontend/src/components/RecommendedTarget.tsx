import type { Technique, TraceResult } from '../api/client'

/** The verdict, stated as a sentence first and a number second (§13). Abstention gets the same
 *  visual weight as an answer — refusing to crown is the disciplined outcome, not an error state.
 *  INCOMPLETE is NOT abstention: the data never arrived, and the headline says so. */
export function RecommendedTarget({ r, techs, flashKey, onRetrace }: {
  r: TraceResult; techs: Technique[]; flashKey: number; onRetrace?: () => void
}) {
  const top = (r.vasp_candidates ?? []).find((c) => c.entity === r.recommended)
  const dep = r.nearest?.deposit_event ?? r.deposit_event
  const asset = dep?.amount_btc != null ? 'BTC' : (r.nearest?.path?.[0]?.asset ?? '')
  const incomplete = !r.recommended && (r.state === 'INCOMPLETE' || r.partial)
  const kinds = [...new Map(techs.map((t) => [t.kind, t])).values()]
  const stop = techs.find((t) => ['coinjoin', 'service_hub', 'mixer', 'bridge'].includes(t.kind))
  const routable = top?.sahyog === 'confirmed'

  return (
    <section key={flashKey} className={`verdict flash ${r.recommended ? '' : incomplete ? 'abstain incomplete' : 'abstain'}`}
             aria-label="verdict">
      <div className="kicker">
        <span className="idx mono dim">01</span>
        <span className={`tag ${r.recommended ? 'green' : incomplete ? 'red' : 'amber'}`}>{r.state}</span>
        <span className="mono">{r.wallet}</span>
        <span className="tag dim mono">{r.chain} · block {r.pins.snapshot_block}</span>
        {onRetrace && (
          <button className="ghost" style={{ marginLeft: 'auto', fontSize: 12 }} onClick={onRetrace}
                  title="force a fresh run at the same snapshot; replays from the evidence store">
            Re-trace live ↻
          </button>
        )}
      </div>

      {r.recommended ? (
        <h1 className="headline">
          Funds reached <em>{top?.entity_name ?? r.recommended}</em> in {r.hops ?? r.nearest?.hops} hop{(r.hops ?? 0) === 1 ? '' : 's'}.
          {' '}<span className="dim">Serve {top?.entity_name ?? r.recommended} for the account holder.</span>
        </h1>
      ) : incomplete ? (
        <h1 className="headline"><em>Incomplete trace.</em> <span className="dim">Part of the graph was never fetched. Re-run before concluding anything.</span></h1>
      ) : (r.vasp_candidates?.length ?? 0) >= 2 ? (
        <h1 className="headline">
          <em>No exchange named.</em>{' '}
          <span className="dim">{r.vasp_candidates[0].entity_name} and {r.vasp_candidates[1].entity_name} are {r.separation_pts} points
            apart: too close to call, so the engine abstains.</span>
        </h1>
      ) : (
        <h1 className="headline">
          <em>No exchange named.</em>{' '}
          <span className="dim">{stop ? `The trail ends at a ${stop.title.toLowerCase()}. Following it further would be a guess.`
            : 'No labeled or sweep-provable deposit address was reached.'}</span>
        </h1>
      )}

      <p className="sub">{r.rationale ?? r.reason}</p>

      <div className="metrics">
        {top && (
          <div className="metric">
            <div className="k">confidence index</div>
            <div className="v">{top.score}<small> / 100</small></div>
          </div>
        )}
        <div className="metric">
          <div className="k">separation #1 vs #2</div>
          <div className="v">{r.separation ?? 'n/a'}<small>{r.separation_pts != null ? ` ${r.separation_pts} pts` : (r.vasp_candidates?.length === 1 ? ' single candidate' : '')}</small></div>
        </div>
        {dep && (
          <div className="metric">
            <div className="k">value at endpoint</div>
            <div className="v">{dep.amount_btc ?? dep.amount}<small> {asset}</small></div>
          </div>
        )}
        {r.recommended && (
          <div className="metric">
            <div className="k">legal route</div>
            <div className={`v ${routable ? 'green' : 'amber'}`} style={{ fontSize: 15 }}>{routable ? 'SAHYOG portal' : 'MLAT / direct'}</div>
          </div>
        )}
        <div className="metric">
          <div className="k">addresses walked</div>
          <div className="v">{r.addresses_seen}<small> · {r.api_calls} reads</small></div>
        </div>
        <div className="metric">
          <div className="k">time to lead</div>
          <div className="v">{r.wall_clock_s}<small>s · {r.upstream_calls} network</small></div>
        </div>
      </div>

      {kinds.length > 0 && (
        <div className="row" style={{ marginTop: 16, gap: 8 }}>
          <span className="dim" style={{ fontSize: 12 }}>techniques detected</span>
          {kinds.map((t) => (
            <span key={t.kind} className={`tag ${t.basis === 'observed' ? 'green' : t.basis === 'heuristic' ? 'amber' : 'accent'}`}>{t.title}</span>
          ))}
        </div>
      )}
      <div className="note">
        Investigative lead, not identity and not evidence. Value is what landed at the endpoint, not
        the suspect's own share: a deposit transaction can aggregate many senders.
      </div>
    </section>
  )
}
