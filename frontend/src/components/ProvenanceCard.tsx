import { useEffect, useState } from 'react'
import { provenance } from '../api/client'
import type { Provenance, TraceResult } from '../api/client'

const ts = (t: number) => new Date(t * 1000).toISOString().replace('T', ' ').slice(0, 19)

/** Where every claim came from (§13) — the I4C-friendly reproducibility surface.
 *
 *  Fields the backend genuinely does not record come back null (label_source and tier are null on
 *  sweep-derived endpoints). They render as "not recorded" rather than as a blank cell or an
 *  invented value: a provenance card that quietly fills its own gaps is worse than one that admits
 *  them. */
export function ProvenanceCard({ r }: { r: TraceResult }) {
  const [p, setP] = useState<Provenance | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    setP(null); setErr(null)
    provenance(r.trace_id).then(setP).catch((e) => setErr(String(e)))
  }, [r.trace_id])

  if (err) return <div className="panel err">{err}</div>
  if (!p) return <div className="panel dim">loading provenance…</div>

  return (
    <div className="panel">
      <h2>Provenance</h2>

      {p.provenance.length === 0 ? (
        <div className="dim">No candidate endpoint, so there is no label provenance to show.</div>
      ) : (
        <table>
          <thead>
            <tr><th>entity</th><th>endpoint</th><th>role basis</th><th>source</th><th>tier</th><th>index</th></tr>
          </thead>
          <tbody>
            {p.provenance.map((row) => (
              <tr key={row.entity + row.endpoint}>
                <td>{row.entity_name ?? row.entity}</td>
                <td className="mono trunc cyan" style={{ fontSize: 12 }}>{row.endpoint}</td>
                <td>
                  <span className={`tag ${row.role_basis === 'labeled' ? 'green' : 'amber'}`}>
                    {row.role_basis ?? 'not recorded'}
                  </span>
                </td>
                <td className={row.label_source ? '' : 'dim'}>{row.label_source ?? 'not recorded'}</td>
                <td className={row.tier ? '' : 'dim'}>{row.tier ?? 'not recorded'}</td>
                <td className="mono">{row.confidence_index}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {p.sweep_evidence.length > 0 && (
        <>
          <div className="dim" style={{ fontSize: 12, margin: '14px 0 6px' }}>
            sweep evidence — how the endpoint earns the words "deposit address"
          </div>
          {p.sweep_evidence.map((s) => (
            <div key={s.address} style={{ marginBottom: 10 }}>
              <div className="row" style={{ gap: 14 }}>
                <span className="mono cyan trunc">{s.address}</span>
                <span className={`tag ${s.sweep === 'proven' ? 'green' : 'amber'}`}>sweep {s.sweep}</span>
                <span className="dim" style={{ fontSize: 12 }}>
                  {s.distinct_senders} distinct senders{s.senders_truncated ? ' (truncated)' : ''}
                  {' · '}{(s.share * 100).toFixed(2)}% of the sweep tx
                </span>
              </div>
              <div className="note" style={{ marginTop: 4 }}>
                swept to hot wallet <span className="mono">{s.hot_wallet}</span> in tx{' '}
                <span className="mono">{s.sweep_tx.slice(0, 20)}…</span> at {ts(s.sweep_ts)}
                <br />hot-wallet label: <span className="mono">{s.hot_label}</span>
                {s.deposit_event && (
                  <>
                    <br />deposit event <span className="mono">{s.deposit_event.tx.slice(0, 20)}…</span>{' '}
                    {ts(s.deposit_event.ts)} · {s.deposit_event.amount_btc ?? s.deposit_event.amount}
                  </>
                )}
              </div>
            </div>
          ))}
        </>
      )}

      <div className="dim" style={{ fontSize: 12, margin: '14px 0 6px' }}>
        provider response hashes — the cached bytes this trace was computed from
      </div>
      {p.response_hashes.length === 0 ? (
        <div className="note">
          No cached provider rows matched this trace's pinned snapshot. The hash is recorded per
          request at fetch time, so a trace served entirely from an earlier snapshot's rows shows
          none here.
        </div>
      ) : (
        <table>
          <thead><tr><th>provider</th><th>request</th><th>response hash</th><th>fetched</th></tr></thead>
          <tbody>
            {p.response_hashes.map((h) => (
              <tr key={h.request_key}>
                <td>{h.provider}</td>
                <td className="mono trunc" style={{ fontSize: 11 }}>{h.request}</td>
                <td className="mono cyan" style={{ fontSize: 11 }}>{h.content_hash.slice(0, 16)}…</td>
                <td className="dim mono" style={{ fontSize: 11 }}>{String(h.fetched_at).slice(0, 19)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="row" style={{ marginTop: 12, gap: 16, fontSize: 12 }}>
        <span className="dim mono">block {p.pins.snapshot_block}</span>
        <span className="dim mono">{p.pins.label_set_version}</span>
        <span className="dim mono">{p.pins.weight_hash}</span>
        <span className="dim mono">{p.pins.adapter_version}</span>
      </div>
      <div className="note">
        Provenance and reproducibility are not legal chain of custody — production custody,
        retention and DPDP posture are named as future work, not built.
      </div>
    </div>
  )
}
