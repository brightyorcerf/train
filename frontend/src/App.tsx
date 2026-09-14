import { useCallback, useEffect, useRef, useState } from 'react'
import { listCases, traceResult, waitAll } from './api/client'
import type { CaseRow, TraceResult } from './api/client'
import { HUD } from './components/HUD'
import { TraceForm } from './components/TraceForm'
import { CaseList } from './components/CaseList'
import { Leaderboard } from './components/Leaderboard'
import { RecommendedTarget } from './components/RecommendedTarget'
import { NearestPanel } from './components/NearestPanel'
import { GraphView } from './components/GraphView'
import { ScoreBreakdown } from './components/ScoreBreakdown'
import { ProvenanceCard } from './components/ProvenanceCard'
import { ConvergencePanel } from './components/ConvergencePanel'
import { ReportButton } from './components/ReportButton'
import { NetworkCanvas } from './components/NetworkCanvas'

export default function App() {
  const [cases, setCases] = useState<CaseRow[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [result, setResult] = useState<TraceResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [elapsed, setElapsed] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [landed, setLanded] = useState(true)
  const timer = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    try {
      setCases((await listCases()).cases)
    } catch (e) {
      setErr(String(e))
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const select = useCallback(async (id: string) => {
    setSel(id)
    setResult(null)
    setElapsed(null)
    try {
      setResult(await traceResult(id))
    } catch (e) {
      setErr(String(e))
    }
  }, [])

  async function started(ids: string[]) {
    setLanded(false)
    setBusy(true)
    setErr(null)
    setSel(ids[0])
    setResult(null)
    const t0 = performance.now()
    timer.current = window.setInterval(() => setElapsed((performance.now() - t0) / 1000), 100)
    try {
      const final = await waitAll(ids, () => void refresh())
      // A FAILED job has no result to fetch — asking for one would surface a bare 409 instead of
      // the reason the worker actually recorded. Say what broke.
      const dead = Object.entries(final).filter(([, s]) => s.state === 'FAILED')
      if (dead.length) {
        setErr(`trace FAILED — ${dead.map(([i, s]) => `${i.slice(0, 8)}: ${s.error ?? 'no cause recorded'}`).join(' · ')}`)
      } else {
        setResult(await traceResult(ids[0]))
      }
    } catch (e) {
      setErr(String(e))
    } finally {
      if (timer.current) window.clearInterval(timer.current)
      setElapsed((performance.now() - t0) / 1000)
      setBusy(false)
      void refresh()
    }
  }

  if (landed) {
    return (
      <div className="hero">
        <NetworkCanvas />
        <div className="hero-word">train</div>
        <div className="hero-tag">
          An automated blockchain tracing engine that instantly connects illicit, unknown crypto
          wallets to known exchanges
        </div>
        <TraceForm onStarted={started} busy={busy} hero />
      </div>
    )
  }

  return (
    <div className="app">
      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 4 }}>
        <h1 style={{ fontSize: 22, cursor: 'pointer' }} onClick={() => setLanded(true)}>train</h1>
        <span className="dim" style={{ fontSize: 12 }}>VASP attribution · SIH26182</span>
      </div>
      <div className="note" style={{ marginTop: 0, marginBottom: 14 }}>
        Which exchange can identify the account holder behind a suspect wallet — an investigative
        lead, not identity and not evidence.
      </div>

      <HUD r={result} elapsed={elapsed} running={busy} />
      {err && <div className="panel err">{err}</div>}

      <div className="cols">
        <CaseList rows={cases} selected={sel} onSelect={select} />
        {result ? <RecommendedTarget r={result} /> : (
          <div className="panel dim">{busy ? 'tracing…' : 'select a case to see its result'}</div>
        )}
      </div>

      {result && (
        <>
          <Leaderboard r={result} />
          <GraphView r={result} />
          <NearestPanel r={result} />
          <ScoreBreakdown r={result} />
          <ProvenanceCard r={result} />
          <ReportButton r={result} />
        </>
      )}

      <ConvergencePanel cases={cases} />

      <TraceForm
        onStarted={started}
        busy={busy}
        heading={cases.length > 0 ? 'trace another suspect wallet' : 'trace a suspect wallet'}
      />
    </div>
  )
}
