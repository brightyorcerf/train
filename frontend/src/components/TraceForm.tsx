import { useState } from 'react'
import { createCase } from '../api/client'

const GOLDEN = {
  'wuhuihui → binance (ATTRIBUTED)': '12w6v1qAaBc4W8h8C2Cu5SKFaKDSv3erUW',
  'hydra market (UNATTRIBUTED, coinjoin)': '123WBUDmSJv4GctdVEz6Qq6z8nXSKrJ4KX',
}

/** One or more wallets under ONE snapshot — multi-wallet is what makes §8 convergence reachable.
 *  `hero` swaps the panel chrome for the landing page's pill input + gold CTA, same submit logic. */
export function TraceForm({ onStarted, busy, hero = false, heading = 'Trace a suspect wallet' }: {
  onStarted: (ids: string[]) => void
  busy: boolean
  hero?: boolean
  heading?: string
}) {
  const [text, setText] = useState('')
  const [err, setErr] = useState<string | null>(null)

  const wallets = text.split(/[\s,]+/).map((w) => w.trim()).filter(Boolean)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setErr(null)
    try {
      const out = await createCase({ wallets, chain: 'btc' })
      onStarted(out.trace_ids)
    } catch (x) {
      setErr(String(x))
    }
  }

  const [addr] = Object.values(GOLDEN)
  async function traceGolden() {
    setErr(null)
    try {
      const out = await createCase({ wallets: [addr], chain: 'btc' })
      onStarted(out.trace_ids)
    } catch (x) {
      setErr(String(x))
    }
  }

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
          <button aria-label="trace" disabled={busy || wallets.length === 0}>→</button>
        </div>
        <div className="row" style={{ justifyContent: 'center', marginTop: 18 }}>
          <button type="button" className="hero-golden" onClick={traceGolden} disabled={busy}>
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
        <button disabled={busy || wallets.length === 0}>
          {busy ? 'tracing…' : wallets.length > 1 ? `trace ${wallets.length} wallets` : 'trace'}
        </button>
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
