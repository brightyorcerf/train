import { useState } from 'react'
import { convergence, DEMO_SNAPSHOT, intake, waitAll } from '../api/client'
import type { Convergence, IntakeResult } from '../api/client'
import { ConvergenceResult } from './ConvergencePanel'
import { short } from '../fmt'

/** Addresses are the eight Lazarus Group ETH wallets on the OFAC SDN list (DPRK3, 2019-09-13), as in
 *  labels/ofac_sdn_crypto.csv. The wording around them is ours and says so: no victim is invented. */
const SAMPLE = `SAMPLE TEXT, not a real complaint. The addresses are real: eight Ethereum wallets OFAC lists for the Lazarus Group (SDN, 2019-09-13).

Complainant reports being asked to "verify" a trading account by sending ETH to 0x08723392Ed15743cc38513C4925f5e6be5c17243, then to 0x098B716B8Aaf21512996dC57EB0615e2383E2f96 and 0x35fB6f6DB4fb05e6A4cE86f2C93691425626d4b1.
A second chat log gives 0x3Cffd56B47B7b41c56258D9C7731ABaDc360E073, 0x3e37627dEAA754090fBFbb8bd226c1CE66D255e9 and 0x53b6936513e738f44FB50d2b9476730C0Ab3Bfc1.
Later messages: 0xa0e1c89Ef1a489c9C7dE96311eD5Ce5D32c20E4B, 0xF7B31119c2682c88d88D455dBb9d5932c65Cf1bE.`

/** Complaint text -> addresses -> one case per chain -> convergence, without hand-picking traces. */
export function IntakePanel() {
  const [text, setText] = useState('')
  const [res, setRes] = useState<IntakeResult | null>(null)
  const [conv, setConv] = useState<Record<string, Convergence>>({})
  const [stage, setStage] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  async function run() {
    setErr(null); setRes(null); setConv({})
    try {
      setStage('extracting')
      const r = await intake(text, DEMO_SNAPSHOT)
      setRes(r)
      const ids = Object.values(r.cases).flatMap((c) => c.trace_ids)
      setStage(`tracing ${ids.length} wallet${ids.length === 1 ? '' : 's'}`)
      await waitAll(ids, () => {})
      setStage('intersecting')
      const out: Record<string, Convergence> = {}
      for (const [chain, c] of Object.entries(r.cases)) {
        if (c.trace_ids.length >= 2) out[chain] = await convergence(c.trace_ids, chain)
      }
      setConv(out)
    } catch (e) {
      setErr(String(e))
    } finally {
      setStage(null)
    }
  }

  const bad = res?.addresses.filter((a) => !a.valid) ?? []

  return (
    <div className="panel" id="intake">
      <h2>From complaint text: paste it, we find the wallets and where they meet</h2>
      <div className="note" style={{ marginTop: 0 }}>
        Every BTC, Ethereum and Tron address in the text is pulled out and checksum-checked, then
        traced as one case per chain. Where several wallets share a node, that is one campaign.
      </div>
      <textarea
        className="mono"
        rows={6}
        style={{ width: '100%', marginTop: 10 }}
        placeholder="Paste complaint text, a chat export or an email"
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={!!stage}
        aria-label="complaint text"
      />
      <div className="row" style={{ marginTop: 8 }}>
        <button className="primary" disabled={!!stage || !text.trim()} onClick={run}>
          {stage ? `${stage}…` : 'Extract and trace'}
        </button>
        <button disabled={!!stage} onClick={() => setText(SAMPLE)}>Load sample text</button>
      </div>
      {err && <div className="note err">{err}</div>}

      {res && (
        <div style={{ marginTop: 12 }}>
          <b>{res.addresses.length - bad.length}</b>
          <span className="dim"> valid address{res.addresses.length - bad.length === 1 ? '' : 'es'} found
            {Object.keys(res.cases).length > 0 && <> across {Object.keys(res.cases).join(', ')}</>}</span>
          {bad.length > 0 && (
            <div className="note err">
              Rejected: {bad.map((a) => `${short(a.address)} (${a.check})`).join('; ')}
            </div>
          )}
          {res.dropped_over_cap.length > 0 && (
            <div className="note">Over the 10-per-chain cap, not traced: {res.dropped_over_cap.map(short).join(', ')}</div>
          )}
        </div>
      )}

      {Object.entries(conv).map(([chain, out]) => (
        <div key={chain}>
          <div className="dim" style={{ marginTop: 12 }}>{chain}</div>
          <ConvergenceResult out={out} />
        </div>
      ))}
    </div>
  )
}
