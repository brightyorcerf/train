import { useEffect, useState } from 'react'
import { benchmark, createCase } from '../api/client'
import type { BenchCase, Benchmark } from '../api/client'
import { NetworkCanvas } from './NetworkCanvas'
import { TraceForm } from './TraceForm'

const short = (a: string) => `${a.slice(0, 10)}…${a.slice(-6)}`

/** Landing: the pitch, the benchmark as a number, and the eight documented cases one click away.
 *
 *  The scorecard is GET /benchmark — the frozen-weight eval harness run over the golden set from
 *  the raw store — so the headline on screen is the shipped number, not a copy of it. It is phrased
 *  "M of N", never as a percentage (harness rule 1: n is 8). */
export function Landing({ onStarted, busy, onRecent, nRecent }: {
  onStarted: (ids: string[], reused: boolean, wallet: string) => void
  busy: boolean
  onRecent: () => void
  nRecent: number
}) {
  const [b, setB] = useState<Benchmark | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [going, setGoing] = useState<string | null>(null)

  useEffect(() => { benchmark().then(setB).catch((e) => setErr(String(e))) }, [])

  /** Traced at the SAME snapshot the benchmark scored it at, so it replays from the store. The API
   *  is idempotent on (wallet, chain, snapshot): a case already traced returns its existing run. */
  async function open(c: BenchCase) {
    setGoing(c.id); setErr(null)
    try {
      const out = await createCase({ wallets: [c.suspect], chain: c.chain, snapshot_block: c.snapshot })
      onStarted(out.trace_ids, out.cases.every((c) => c.reused), out.cases[0].wallet)
    } catch (e) {
      setErr(String(e)); setGoing(null)
    }
  }

  const s = b?.summary

  return (
    <>
      <div className="hero-band">
        <NetworkCanvas />
        <div className="topbar">
          <span style={{ flex: 1 }} />
          {nRecent > 0 && <button className="ghost" style={{ color: '#fff' }} onClick={onRecent}>Recent traces ({nRecent})</button>}
        </div>
        <section className="hero">
          <h1 className="hero-word">train</h1>
          <p className="lede">Traces a suspect wallet to the exchange that can name its owner. On evidence, or not at all.</p>
          <TraceForm onStarted={onStarted} busy={busy} hero />
          <button className="gold" style={{ padding: '11px 22px', fontSize: 14 }}
                  onClick={() => document.getElementById('cases')?.scrollIntoView({ behavior: 'smooth' })}>
            golden cases ↓
          </button>
          <span className="scroll-hint">8 documented cases below</span>
        </section>
      </div>

    <div className="landing">
      <div className="score" aria-label="benchmark">
        <div className="lead">
          <div className="big">{s ? <>{s.correct}<small> / {s.scored}</small></> : '…'}</div>
          <div className="k">
            OFAC / DOJ-documented cases decided as the government record says, replayed live from
            the evidence store {b && <>in {b.wall_s}s</>}
          </div>
        </div>
        <div>
          <div className="big">{s ? <>{s.discovery_correct}<small> / {s.discovery}</small></> : '…'}</div>
          <div className="k">correct exchange ranked #1{b && s && s.discovery_correct < s.discovery && b.cases.every((c) => c.verdict !== 'WRONG') && (
            <span className="dim">; the rest abstained, none wrong</span>)}</div>
        </div>
        <div>
          <div className="big">{s ? <>{s.confusers_correct}<small> / {s.confusers}</small></> : '…'}</div>
          <div className="k">traps correctly refused: CoinJoin, custodial hub, unnamed services</div>
        </div>
        <div>
          <div className="big">{s ? <>{s.stable}<small> / {s.profiles}</small></> : '…'}</div>
          <div className="k">top-1 unchanged under ±20% weight jitter
            {s && <span className="dim"> ({s.contested} contested case{s.contested === 1 ? '' : 's'})</span>}
          </div>
        </div>
        <div>
          <div className="big">{s ? s.upstream : '…'}</div>
          <div className="k">network calls to reproduce all {s?.cases ?? ''}; {s?.calls ?? '…'} reads served from the
            content-hashed store</div>
        </div>
      </div>

      <div className="gallery-h" id="cases" style={{ scrollMarginTop: 24 }}>
        <h2>Documented cases</h2>
        <span className="dim" style={{ fontSize: 12 }}>
          weights frozen {b?.weights ?? '…'} before the run · n is small: a held-out smoke test, not an
          accuracy figure
        </span>
      </div>
      {err && <div className="err" style={{ marginBottom: 12 }}>{err}</div>}
      <div className="gallery">
        {(b?.cases ?? Array.from({ length: 8 }, () => null)).map((c, i) => c ? (
          <button key={c.id} className={`case ${c.expect === 'UNATTRIBUTED' ? 'confuser' : ''}`}
                  style={{ animationDelay: `${i * 45}ms` }} disabled={busy || !!going} onClick={() => void open(c)}>
            <div className="top">
              <span className={`tag ${c.expect === 'ATTRIBUTED' ? 'green' : 'amber'}`}>
                {c.expect === 'ATTRIBUTED' ? 'discovery' : 'trap'}
              </span>
              <span className="tag dim mono">{c.chain}</span>
            </div>
            <h3>{c.title}</h3>
            <div className="addr">{short(c.suspect)} · block {c.snapshot}</div>
            {c.why && <div className="dim" style={{ fontSize: 12, lineHeight: 1.45 }}>{c.why.split(/[.;]/)[0]}</div>}
            <div className="outcome">
              <span>
                {c.verdict === 'ABSTAINED'
                  ? <span className="amber">abstained: two exchanges too close to call</span>
                  : c.expect === 'ATTRIBUTED'
                    ? <>→ <b className="gold">{c.recommended}</b>{c.hops != null && <span className="dim"> · {c.hops} hops</span>}</>
                    : <span className="amber">no target crowned</span>}
                {' '}
                {c.verdict === 'CORRECT' ? <span className="green">✓</span> : c.verdict !== 'ABSTAINED' && <span className="red">{c.verdict}</span>}
              </span>
              <span className="go">{going === c.id ? 'opening…' : 'trace →'}</span>
            </div>
          </button>
        ) : <div key={i} className="skeleton" style={{ minHeight: 190 }} />)}
      </div>
    </div>
    </>
  )
}
