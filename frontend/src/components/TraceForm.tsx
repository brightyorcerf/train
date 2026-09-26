import { useState } from 'react'
import { createCase, DEMO_SNAPSHOT } from '../api/client'

const CHAINS = ['btc', 'eth', 'polygon'] as const

/** One or more wallets under ONE snapshot — multi-wallet is what makes §8 convergence reachable.
 *  The golden cases are no longer buttons here: they live in the landing gallery, pinned to the
 *  snapshot the benchmark scored them at. */
export function TraceForm({ onStarted, busy, hero = false }: {
  onStarted: (ids: string[], reused: boolean, wallet: string) => void
  busy: boolean
  hero?: boolean
}) {
  const [text, setText] = useState('')
  const [chain, setChain] = useState<string>('btc')
  const [err, setErr] = useState<string | null>(null)
  const wallets = text.split(/[\s,]+/).map((w) => w.trim()).filter(Boolean)

  /** Always pins a snapshot. Letting the API pin the live tip meant every run keyed a scope the
   *  raw store had never seen, so the "offline" demo silently went to the network. */
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setErr(null)
    try {
      const out = await createCase({ wallets, chain, snapshot_block: DEMO_SNAPSHOT[chain] })
      onStarted(out.trace_ids, out.cases.every((c) => c.reused), out.cases[0].wallet)
      setText('')
    } catch (x) {
      setErr(String(x))
    }
  }

  return (
    <>
      <form onSubmit={submit} aria-label="trace a wallet">
        <input
          className="mono"
          placeholder={hero ? 'Paste a suspect wallet address, or several' : 'Trace another wallet…'}
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={busy}
          aria-label="wallet address"
        />
        <select value={chain} onChange={(e) => setChain(e.target.value)} disabled={busy} aria-label="chain">
          {CHAINS.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <button className="primary" disabled={busy || wallets.length === 0}>
          {busy ? 'Tracing…' : wallets.length > 1 ? `Trace ${wallets.length}` : 'Trace'}
        </button>
      </form>
      {err && <div className="err" style={{ marginTop: 8 }}>{err}</div>}
    </>
  )
}
