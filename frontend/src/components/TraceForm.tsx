import { useState } from 'react'
import { createCase } from '../api/client'

const GOLDEN = {
  'wuhuihui → binance (ATTRIBUTED)': '12w6v1qAaBc4W8h8C2Cu5SKFaKDSv3erUW',
  'hydra market (UNATTRIBUTED, coinjoin)': '123WBUDmSJv4GctdVEz6Qq6z8nXSKrJ4KX',
}

/** One or more wallets under ONE snapshot — multi-wallet is what makes §8 convergence reachable. */
export function TraceForm({ onStarted, busy }: { onStarted: (ids: string[]) => void; busy: boolean }) {
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

  return (
    <form className="panel" onSubmit={submit}>
      <h2>Trace a suspect wallet</h2>
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
