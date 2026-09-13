import { useCallback, useEffect, useRef, useState } from 'react'
import { rescore } from '../api/client'
import type { Rescore, TraceResult } from '../api/client'

/** The calibration answer, made movable (§11.2 / Q&A #3).
 *
 *  The sliders do NOT recompute the score in the browser. They post the weight profile to the
 *  backend, which re-runs `attribute_result` — the same function the eval harness calls. That
 *  keeps one implementation of the scoring arithmetic instead of a JS copy that would drift from
 *  it (and would round differently: Python's round() is half-to-even, JS's Math.round is not, so a
 *  contribution sum of exactly 0.705 renders 70 on the backend and would render 71 here).
 *
 *  Nothing here mutates the frozen weights. The pinned profile stays FROZEN at w-75bf07eaee79;
 *  this is a what-if over a stored trace, which is why it costs zero provider calls. */
export function ScoreBreakdown({ r }: { r: TraceResult }) {
  const top = (r.vasp_candidates ?? []).find((c) => c.entity === r.recommended) ?? r.vasp_candidates?.[0]
  const frozen = top?.breakdown?.weights
  const [pct, setPct] = useState<Record<string, number>>({})
  const [out, setOut] = useState<Rescore | null>(null)
  const [profiles, setProfiles] = useState<Rescore | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const t = useRef<number | null>(null)

  const send = useCallback((next: Record<string, number>) => {
    if (!frozen) return
    if (t.current) window.clearTimeout(t.current)
    t.current = window.setTimeout(async () => {
      try {
        const weights = Object.fromEntries(
          Object.entries(frozen).map(([k, v]) => [k, v * (1 + (next[k] ?? 0) / 100)]))
        setOut(await rescore(r.trace_id, { weights }))
        setErr(null)
      } catch (e) {
        setErr(String(e))
      }
    }, 120)
  }, [frozen, r.trace_id])

  // A fresh trace is a fresh what-if: drop any perturbation carried over from the last case.
  useEffect(() => { setPct({}); setOut(null); setProfiles(null); setErr(null) }, [r.trace_id])

  if (!top || !frozen) {
    return (
      <div className="panel">
        <h2>Score breakdown</h2>
        <div className="dim">No candidate was scored, so there is no arithmetic to show.</div>
      </div>
    )
  }

  const br = top.breakdown
  const moved = Object.values(pct).some((v) => v !== 0)
  const held = out ? out.rank_unchanged : true

  return (
    <div className="panel">
      <h2>Score breakdown — {top.entity_name ?? top.entity}</h2>
      <table>
        <thead>
          <tr><th>factor</th><th>value</th><th>weight</th><th>contribution</th><th style={{ width: 210 }}>perturb ±20%</th></tr>
        </thead>
        <tbody>
          {Object.keys(frozen).map((k) => {
            const d = pct[k] ?? 0
            return (
              <tr key={k}>
                <td>{k.replace(/_/g, ' ')}</td>
                <td className="mono">{br.factors[k]}</td>
                <td className="mono">
                  {frozen[k]}
                  {d !== 0 && <span className="amber"> → {(frozen[k] * (1 + d / 100)).toFixed(4)}</span>}
                </td>
                <td className="mono">{br.contributions[k]}</td>
                <td>
                  <div className="row" style={{ gap: 8, flexWrap: 'nowrap' }}>
                    <input
                      type="range" min={-20} max={20} step={1} value={d}
                      style={{ minWidth: 0, flex: 1, padding: 0 }}
                      onChange={(ev) => {
                        const next = { ...pct, [k]: Number(ev.target.value) }
                        setPct(next)
                        send(next)
                      }} />
                    <span className="mono dim" style={{ width: 38, textAlign: 'right' }}>
                      {d > 0 ? `+${d}` : d}%
                    </span>
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      <div className="row" style={{ marginTop: 12, gap: 16 }}>
        <span>
          <span className="dim">frozen </span>
          <b className="mono">{top.score}</b><span className="dim"> / 100</span>
        </span>
        {out && (
          <span>
            <span className="dim">perturbed </span>
            <b className={`mono ${out.ranked[0]?.score === top.score ? '' : 'amber'}`}>
              {out.ranked.find((x) => x.entity === top.entity)?.score ?? '—'}
            </b><span className="dim"> / 100</span>
          </span>
        )}
        <span className={`tag ${held ? 'green' : 'red'}`}>
          {moved ? (held ? 'ranking holds' : 'RANKING CHANGED') : 'unperturbed'}
        </span>
        <span className="dim mono" style={{ fontSize: 11 }}>{br.weight_hash} frozen</span>
        {moved && (
          <button onClick={() => { setPct({}); setOut(null) }} style={{ marginLeft: 'auto' }}>
            reset to frozen
          </button>
        )}
      </div>

      {out && (
        <div className="row" style={{ marginTop: 8, gap: 10 }}>
          {out.ranked.slice(0, 4).map((c, i) => (
            <span key={c.entity} className="mono" style={{ fontSize: 12 }}>
              <span className="dim">{i + 1}. </span>{c.entity_name ?? c.entity} {c.score}
            </span>
          ))}
          <span className="dim" style={{ fontSize: 12 }}>
            crowned: {out.recommended ?? 'none (abstained)'}
          </span>
        </div>
      )}

      <div className="row" style={{ marginTop: 12 }}>
        <button onClick={async () => {
          try {
            setProfiles(await rescore(r.trace_id, { perturb_pct: 0.2, profiles: 12 }))
            setErr(null)
          } catch (e) { setErr(String(e)) }
        }}>run the harness's 12 seeded ±20% profiles</button>
        {profiles?.stability && (
          <span className={`tag ${profiles.stability.unchanged === profiles.stability.n ? 'green' : 'amber'}`}>
            top-1 unchanged in {profiles.stability.unchanged} of {profiles.stability.n} profiles
          </span>
        )}
      </div>

      {err && <div className="note err">{err}</div>}

      <div className="note">
        Each profile jitters every weight by up to ±20% and renormalizes to 1 — the same operation
        the eval harness runs, executed here by the same backend function, so the control on screen
        and the shipped golden-set number are one test rather than two. The frozen profile is never
        modified: this is a what-if over a stored trace, which is why it costs zero provider calls.
      </div>
    </div>
  )
}
