import { useState } from 'react'
import { createCase, DEMO_SNAPSHOT } from '../api/client'

const GOLDEN = {
  'wuhuihui → binance (ATTRIBUTED)': '12w6v1qAaBc4W8h8C2Cu5SKFaKDSv3erUW',
  'hydra market (UNATTRIBUTED, coinjoin)': '123WBUDmSJv4GctdVEz6Qq6z8nXSKrJ4KX',
}

const CHAINS = ['btc', 'eth', 'polygon'] as const

/** One or more wallets under ONE snapshot — multi-wallet is what makes §8 convergence reachable.
 *  `hero` swaps the panel chrome for the landing page's pill input + gold CTA, same submit logic. */
export function TraceForm({ onStarted, busy, hero = false, heading = 'Trace a suspect wallet' }: {
  onStarted: (ids: string[]) => void
  busy: boolean
  hero?: boolean
  heading?: string
}) {
  const [text, setText] = useState('')
  const [chain, setChain] = useState<string>('btc')
  const [err, setErr] = useState<string | null>(null)

  const wallets = text.split(/[\s,]+/).map((w) => w.trim()).filter(Boolean)

  /** Always pins a snapshot. Letting the API pin the live tip meant every run keyed a scope the
   *  raw store had never seen, so the "offline" demo silently went to the network. */
  async function run(w: string[], c: string) {
    setErr(null)
    try {
      const out = await createCase({ wallets: w, chain: c, snapshot_block: DEMO_SNAPSHOT[c] })
      onStarted(out.trace_ids)
    } catch (x) {
      setErr(String(x))
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    await run(wallets, chain)
  }

  const [addr] = Object.values(GOLDEN)

  if (hero) {
    return (
      <form onSubmit={submit}>
        <div className="hero-input">
          <input
            placeholder="enter your wallet here"
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={busy}
          />
          <button aria-label="trace wallet" disabled={busy || wallets.length === 0}>→</button>
        </div>
        <div className="row" style={{ justifyContent: 'center', marginTop: 18 }}>
          <button type="button" className="hero-golden" onClick={() => void run([addr], 'btc')}
                  disabled={busy}>
            golden cases →
          </button>
        </div>
        {err && <div className="err" style={{ marginTop: 10, color: '#fff' }}>{err}</div>}
      </form>
    )
  }

  return (
    <form className="panel" onSubmit={submit}>
      <h2>{heading}</h2>
      <div className="row">
        <input
          className="mono"
          placeholder="wallet address — or several, separated by space/comma"
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={busy}
        />
        <label className="dim" style={{ fontSize: 12 }}>
          chain{' '}
          <select value={chain} onChange={(e) => setChain(e.target.value)} disabled={busy}>
            {CHAINS.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <button disabled={busy || wallets.length === 0}>
          {busy ? 'tracing…' : wallets.length > 1 ? `trace ${wallets.length} wallets` : 'trace'}
        </button>
      </div>
      <div className="note" style={{ marginTop: 6 }}>
        Pinned to snapshot {DEMO_SNAPSHOT[chain]} so the trace replays from the stored responses
        (§12) instead of spending provider calls on a fresh chain tip.
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <span className="dim" style={{ fontSize: 12 }}>golden cases:</span>
        {Object.entries(GOLDEN).map(([label, addr]) => (
          <button key={addr} type="button" style={{ fontSize: 12, padding: '4px 8px' }}
                  onClick={() => setText(addr)} disabled={busy}>
            {label}
          </button>
        ))}
      </div>
      {wallets.length > 1 && (
        <div className="note">
          {wallets.length} wallets trace under one pinned snapshot, so their subgraphs are
          comparable — that is the precondition for convergence (§8).
        </div>
      )}
      {err && <div className="err" style={{ marginTop: 8 }}>{err}</div>}
    </form>
  )
}
