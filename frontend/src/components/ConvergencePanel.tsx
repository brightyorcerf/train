import { useState } from 'react'
import { convergence } from '../api/client'
import type { CaseRow, Convergence } from '../api/client'

const short = (a: string) => (a.length > 20 ? `${a.slice(0, 10)}…${a.slice(-6)}` : a)

const ROLE_CLASS: Record<string, string> = {
  mixer: 'red', sanctioned: 'red', bridge: 'amber', dex: 'amber',
  deposit: 'green', hot: 'green', unlabeled: 'dim',
}

/** Multi-victim convergence (§8): N complaints, one shared node.
 *
 *  What the shared node turns out to BE is not ours to choose. On the Lazarus set every trace stops
 *  at Tornado Cash, so the honest ending is "8 complaints -> shared mixer boundary", not
 *  "-> one VASP to serve". The VASP line below renders only when a shared node actually carries a
 *  deposit/hot role; the rest of the time the panel says so plainly rather than implying a
 *  disclosure target that the evidence never reached. */
export function ConvergencePanel({ cases }: { cases: CaseRow[] }) {
  const done = cases.filter((c) => c.state === 'DONE')
  const [picked, setPicked] = useState<string[]>([])
  const [out, setOut] = useState<Convergence | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const chain = done.find((c) => c.trace_id === picked[0])?.chain
  const eligible = done.filter((c) => !chain || c.chain === chain)

  async function run() {
    setBusy(true); setErr(null)
    try {
      setOut(await convergence(picked, chain ?? 'btc'))
    } catch (e) {
      setErr(String(e)); setOut(null)
    } finally {
      setBusy(false)
    }
  }

  const vasp = out?.shared_nodes.filter((n) => !n.is_traced_wallet && (n.role === 'deposit' || n.role === 'hot')) ?? []
  const discovered = out?.shared_nodes.filter((n) => !n.is_traced_wallet) ?? []

  return (
    <div className="panel">
      <h2>Convergence — where separate complaints turn out to be one campaign</h2>

      <div className="note" style={{ marginTop: 0 }}>
        Pick two or more finished traces on the same chain. The intersection runs over stored
        subgraphs in Postgres and contacts no provider, so it costs zero API calls.
      </div>

      <div style={{ maxHeight: 150, overflow: 'auto', margin: '10px 0' }}>
        <table>
          <tbody>
            {eligible.map((c) => (
              <tr key={c.trace_id} className="clickable" onClick={() => {
                setPicked((p) => p.includes(c.trace_id)
                  ? p.filter((x) => x !== c.trace_id)
                  : [...p, c.trace_id])
                setOut(null)
              }}>
                <td style={{ width: 26 }}>
                  <input type="checkbox" readOnly checked={picked.includes(c.trace_id)}
                         style={{ minWidth: 0, flex: 'none' }} />
                </td>
                <td className="mono" style={{ fontSize: 12 }}>{short(c.wallet)}</td>
                <td className="dim">{c.chain}</td>
                <td className="dim" style={{ fontSize: 12 }}>{c.result_state}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="row">
        <button disabled={picked.length < 2 || busy} onClick={run}>
          {busy ? 'intersecting…' : `find shared nodes (${picked.length} selected)`}
        </button>
        {picked.length > 0 && <button onClick={() => { setPicked([]); setOut(null) }}>clear</button>}
      </div>

      {err && <div className="note err">{err}</div>}

      {out && (
        <>
          <div className="row" style={{ margin: '14px 0 4px', gap: 10, alignItems: 'baseline' }}>
            <b style={{ fontSize: 18 }}>{out.trace_ids.length}</b>
            <span className="dim">complaints →</span>
            <b className="cyan" style={{ fontSize: 18 }}>{out.n_shared}</b>
            <span className="dim">shared node{out.n_shared === 1 ? '' : 's'} →</span>
            {vasp.length > 0 ? (
              <b className="gold" style={{ fontSize: 18 }}>
                {vasp[0].entity_name ?? vasp[0].entity}
                {vasp[0].sahyog === 'confirmed' && <span className="tag green" style={{ marginLeft: 8 }}>sahyog</span>}
              </b>
            ) : (
              <span className="amber">no shared VASP endpoint</span>
            )}
          </div>

          {out.n_shared === 0 ? (
            <div className="note">
              These traces share no node. That is a real answer: separate complaints that do not
              converge are separate cases, and the panel will not manufacture a link between them.
            </div>
          ) : (
            <table>
              <thead>
                <tr><th>address</th><th>shared by</th><th>role</th><th>received</th><th>hop</th><th>what this is</th></tr>
              </thead>
              <tbody>
                {discovered.slice(0, 10).map((n) => (
                  <tr key={n.address}>
                    <td className="mono trunc" style={{ fontSize: 12 }}>{short(n.address)}</td>
                    <td className="mono"><b>{n.shared_by}</b><span className="dim"> / {out.trace_ids.length}</span></td>
                    <td>
                      <span className={`tag ${ROLE_CLASS[n.role] ?? 'dim'}`}>
                        {n.entity_name ? `${n.role}: ${n.entity_name}` : n.role}
                      </span>
                    </td>
                    <td className="mono">{n.total_received} {n.asset}</td>
                    <td className="mono dim">{n.min_hop}</td>
                    <td className="dim" style={{ fontSize: 12 }}>{n.interpretation}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {vasp.length === 0 && out.n_shared > 0 && (
            <div className="note">
              The shared node is a boundary, not a disclosure target: the traces converge before any
              of them reaches a VASP. One SAHYOG request cannot cover these cases — what they share
              is where the money stopped being followable (§9.3), and saying so is the difference
              between an insight and a false lead.
            </div>
          )}
          <div className="note">
            A shared node is not automatically a suspect — an exchange hot wallet is shared by
            everyone. Rows that are themselves traced wallets are excluded from the count above.
          </div>
        </>
      )}
    </div>
  )
}
