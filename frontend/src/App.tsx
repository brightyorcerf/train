import { useCallback, useEffect, useRef, useState } from 'react'
import { listCases, traceResult, waitAll } from './api/client'
import type { CaseRow, TraceResult } from './api/client'
import { HUD } from './components/HUD'
import { TraceForm } from './components/TraceForm'
import { CaseList } from './components/CaseList'
import { Leaderboard } from './components/Leaderboard'
import { RecommendedTarget } from './components/RecommendedTarget'
import { NearestPanel } from './components/NearestPanel'
import { ScoreBreakdown } from './components/ScoreBreakdown'
import { ProvenanceCard } from './components/ProvenanceCard'
import { ConvergencePanel } from './components/ConvergencePanel'
import { ReportButton } from './components/ReportButton'

export default function App() {
  const [cases, setCases] = useState<CaseRow[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [result, setResult] = useState<TraceResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [elapsed, setElapsed] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)
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
    setBusy(true)
    setErr(null)
    setSel(ids[0])
    setResult(null)
    const t0 = performance.now()
    timer.current = window.setInterval(() => setElapsed((performance.now() - t0) / 1000), 100)
    try {
      await waitAll(ids, () => void refresh())
      setResult(await traceResult(ids[0]))
    } catch (e) {
      setErr(String(e))
    } finally {
      if (timer.current) window.clearInterval(timer.current)
      setElapsed((performance.now() - t0) / 1000)
      setBusy(false)
      void refresh()
    }
  }

  return (
    <div className="app">
      <h1 style={{ fontSize: 18, marginBottom: 4 }}>
        VASP Attribution <span className="dim" style={{ fontWeight: 400 }}>· SIH26182</span>
      </h1>
      <div className="note" style={{ marginTop: 0, marginBottom: 14 }}>
        Which exchange can identify the account holder behind a suspect wallet — an investigative
        lead, not identity and not evidence.
      </div>

      <HUD r={result} elapsed={elapsed} running={busy} />
      {err && <div className="panel err">{err}</div>}

      <TraceForm onStarted={started} busy={busy} />

      <div className="cols">
        <CaseList rows={cases} selected={sel} onSelect={select} />
        {result ? <RecommendedTarget r={result} /> : (
          <div className="panel dim">{busy ? 'tracing…' : 'select a case to see its result'}</div>
        )}
      </div>

      {result && (
        <>
          <Leaderboard r={result} />
          <NearestPanel r={result} />
          <ScoreBreakdown r={result} />
          <ProvenanceCard r={result} />
          <ReportButton r={result} />
        </>
      )}

      <ConvergencePanel cases={cases} />
    </div>
  )
}
