import { useEffect, useState } from 'react'
import { disclosure, onboarding, reportUrl } from '../api/client'
import type { Disclosure, Onboarding, TraceResult } from '../api/client'

/** The filed artifact (§13) plus the routing answer (§17), with BOTH branches visible.
 *
 *  Both routes are computed from the real registry, never mocked against each other: an onboarded
 *  VASP fires the SAHYOG portal under BNSS S.94, everyone else gets the honest MLAT boundary. Our
 *  golden cases all crown Binance, which is NOT on the portal — so the demo's own case takes the
 *  MLAT branch, and the portal branch is shown alongside it on a VASP that genuinely is onboarded.
 *  Quietly routing an un-onboarded VASP through the portal would be the one claim in this project
 *  that could not survive scrutiny (§18). */
const CONTRAST = 'wazirx'   // confirmed SAHYOG-onboarded in the label set; jurisdiction IN

export function ReportButton({ r }: { r: TraceResult }) {
  const [d, setD] = useState<Disclosure | null>(null)
  const [alt, setAlt] = useState<Onboarding | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    setD(null); setErr(null)
    disclosure(r.trace_id).then(setD).catch((e) => setErr(String(e)))
    onboarding(CONTRAST).then(setAlt).catch(() => setAlt(null))
  }, [r.trace_id])

  const p = d?.disclosure_payload
  const routable = p?.routable_via_sahyog ?? false

  return (
    <div className="panel">
      <h2>Report &amp; routing</h2>

      <div className="row">
        <a href={reportUrl(r.trace_id)} target="_blank" rel="noreferrer">
          <button>open PDF report</button>
        </a>
        <span className="note" style={{ marginTop: 0 }}>
          Four determinism pins on the face — re-running with them reproduces the report.
        </span>
      </div>

      {err && <div className="note err">{err}</div>}

      {p && (
        <>
          <div className="dim" style={{ fontSize: 12, margin: '14px 0 6px' }}>
            this case — {p.target_vasp ?? 'no target crowned'}
          </div>
          <div className="panel" style={{
            margin: 0, borderColor: routable ? 'var(--green)' : 'var(--amber)', background: 'var(--panel-2)',
          }}>
            <span className={`tag ${routable ? 'green' : 'amber'}`}>
              {routable ? 'SAHYOG portal' : 'MLAT / direct legal process'}
            </span>
            <div className="note" style={{ marginTop: 6 }}>{p.route}</div>
          </div>

          {alt && (
            <>
              <div className="dim" style={{ fontSize: 12, margin: '12px 0 6px' }}>
                contrast branch — {alt.target_vasp_name} ({alt.jurisdiction.join(', ') || 'jurisdiction unrecorded'})
              </div>
              <div className="panel" style={{
                margin: 0, borderColor: alt.routable_via_sahyog ? 'var(--green)' : 'var(--amber)',
                background: 'var(--panel-2)',
              }}>
                <span className={`tag ${alt.routable_via_sahyog ? 'green' : 'amber'}`}>
                  {alt.routable_via_sahyog ? 'SAHYOG portal' : 'MLAT / direct legal process'}
                </span>
                <div className="note" style={{ marginTop: 6 }}>{alt.route}</div>
              </div>
            </>
          )}

          <div className="note">
            {d?.note} {p.schema_note}
          </div>
        </>
      )}
    </div>
  )
}
