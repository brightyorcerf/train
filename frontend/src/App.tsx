import { useCallback, useEffect, useRef, useState } from 'react'
import { createCase, listCases, techniques, traceResult, waitAll } from './api/client'
import type { CaseRow, Technique, TraceResult } from './api/client'
import { HUD } from './components/HUD'
import { TraceForm } from './components/TraceForm'
import { Leaderboard } from './components/Leaderboard'
import { RecommendedTarget } from './components/RecommendedTarget'
import { NearestPanel } from './components/NearestPanel'
import { GraphView, LiveGraph } from './components/GraphView'
import { ScoreBreakdown } from './components/ScoreBreakdown'
import { ProvenanceCard } from './components/ProvenanceCard'
import { ReportButton } from './components/ReportButton'
import { Landing } from './components/Landing'
import { RecentDrawer } from './components/RecentDrawer'

export default function App() {
  const [cases, setCases] = useState<CaseRow[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [result, setResult] = useState<TraceResult | null>(null)
  const [techs, setTechs] = useState<Technique[]>([])
  const [live, setLive] = useState<{ id: string; wallet: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [elapsed, setElapsed] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [landed, setLanded] = useState(true)
  const [drawer, setDrawer] = useState(false)
  const [flash, setFlash] = useState(0)
  const timer = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    try { setCases((await listCases()).cases) } catch (e) { setErr(String(e)) }
  }, [])
  useEffect(() => { void refresh() }, [refresh])

  /** Load a finished trace and SAY that the report changed: scroll to the top and flash the
   *  verdict. The old console swapped the report in place off-screen, below the fold. */
  const show = useCallback(async (id: string) => {
    const [r, t] = await Promise.all([traceResult(id), techniques(id).catch(() => ({ techniques: [] }))])
    setResult(r); setTechs(t.techniques); setFlash((f) => f + 1)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [])

  const select = useCallback(async (id: string) => {
    setLanded(false); setDrawer(false); setSel(id); setElapsed(null); setErr(null)
    try { await show(id) } catch (e) { setErr(String(e)) }
  }, [show])

  async function started(ids: string[], reused: boolean, wallet: string) {
    setLanded(false); setBusy(true); setErr(null); setSel(ids[0]); setResult(null); setTechs([])
    window.scrollTo({ top: 0, behavior: 'smooth' })
    // An already-traced case (idempotent re-post) has nothing left to stream: go straight to it.
    if (!reused) setLive({ id: ids[0], wallet })
    const t0 = performance.now()
    timer.current = window.setInterval(() => setElapsed((performance.now() - t0) / 1000), 100)
    try {
      const final = await waitAll(ids, () => undefined)
      // A FAILED job has no result to fetch — say what broke instead of a bare 409.
      const dead = Object.entries(final).filter(([, s]) => s.state === 'FAILED')
      if (dead.length) {
        setErr(`trace FAILED: ${dead.map(([i, s]) => `${i.slice(0, 8)}: ${s.error ?? 'no cause recorded'}`).join(' · ')}`)
      } else {
        await show(ids[0])
      }
    } catch (e) {
      setErr(String(e))
    } finally {
      if (timer.current) window.clearInterval(timer.current)
      setElapsed(reused ? null : (performance.now() - t0) / 1000)
      setBusy(false); setLive(null)
      void refresh()
    }
  }

  const drawerEl = drawer && (
    <RecentDrawer cases={cases} selected={sel} onSelect={(id) => void select(id)} onClose={() => setDrawer(false)} />
  )

  if (landed) {
    return (
      <>
        <Landing onStarted={started} busy={busy} onRecent={() => setDrawer(true)} nRecent={cases.length} />
        {drawerEl}
      </>
    )
  }

  return (
    <>
    <header className="skybar">
      <div className="topbar">
        <button className="wordmark" onClick={() => setLanded(true)} aria-label="back to cases">train</button>
        <TraceForm onStarted={started} busy={busy} />
        <button className="ghost" onClick={() => setDrawer(true)}>Recent traces ({cases.length})</button>
      </div>
    </header>
    <div className="app">

      <HUD r={result} elapsed={elapsed} running={busy} />
      {err && <div className="panel err">{err}</div>}

      {live && <LiveGraph traceId={live.id} wallet={live.wallet} />}
      {busy && !live && <div className="skeleton" style={{ height: 260, marginBottom: 16 }} />}

      {result && (
        <main>
          <RecommendedTarget r={result} techs={techs} flashKey={flash} onRetrace={busy ? undefined : async () => {
            // same wallet, same pinned snapshot: a forced re-run replays from the store, so the live
            // SSE view can be shown with no network at all
            try {
              const out = await createCase({ wallets: [result.wallet], chain: result.chain,
                snapshot_block: result.pins.snapshot_block, force: true })
              void started(out.trace_ids, false, result.wallet)
            } catch (e) { setErr(String(e)) }
          }} />
          <GraphView r={result} techs={techs} />

          <div className="section-h" style={{ marginTop: 28 }}><span className="idx">03</span> Why this target</div>
          <Leaderboard r={result} />
          <div className="cols" style={{ marginBottom: 16 }}>
            <NearestPanel r={result} />
            <ScoreBreakdown r={result} />
          </div>

          <div className="section-h" style={{ marginTop: 28 }}><span className="idx">04</span> Reproduce it, file it</div>
          <ProvenanceCard r={result} />
          <ReportButton r={result} />
        </main>
      )}
      {drawerEl}
    </div>
    </>
  )
}
