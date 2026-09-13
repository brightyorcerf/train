import type { CaseRow } from '../api/client'

const short = (s: string) => `${s.slice(0, 10)}…${s.slice(-6)}`

function stateTag(row: CaseRow) {
  const s = row.result_state ?? row.state
  const cls = s === 'ATTRIBUTED' ? 'green' : s === 'UNATTRIBUTED' ? 'amber'
    : s === 'FAILED' ? 'red' : s.startsWith('BROKEN') ? 'red' : 'cyan'
  return <span className={`tag ${cls}`}>{s}</span>
}

export function CaseList({ rows, selected, onSelect }: {
  rows: CaseRow[]
  selected: string | null
  onSelect: (traceId: string) => void
}) {
  return (
    <div className="panel">
      <h2>Cases ({rows.length})</h2>
      <div style={{ maxHeight: 320, overflowY: 'auto' }}>
        <table>
          <thead>
            <tr><th>wallet</th><th>chain</th><th>state</th><th>target</th><th>block</th></tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.trace_id}
                  className={`clickable ${selected === c.trace_id ? 'sel' : ''}`}
                  onClick={() => onSelect(c.trace_id)}>
                <td className="mono trunc">{short(c.wallet)}</td>
                <td className="dim">{c.chain}</td>
                <td>{stateTag(c)}</td>
                <td className={c.recommended ? 'gold' : 'dim'}>{c.recommended ?? '—'}</td>
                <td className="mono dim">{c.snapshot_block}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={5} className="dim">no cases yet</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
