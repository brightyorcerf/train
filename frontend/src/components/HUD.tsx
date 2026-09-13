import type { TraceResult } from '../api/client'

/** Persistent cost/effort strip (§13). `wall_clock_s` is 0 on chord-driven traces — only the CLI
 *  sets it — so elapsed is the client's own timer for runs started in this session, and is left
 *  blank rather than faked for traces loaded from cache. */
export function HUD({ r, elapsed, running }: { r: TraceResult | null; elapsed: number | null; running: boolean }) {
  const phaseTotal = r ? Object.values(r.phases ?? {}).reduce((a, b) => a + b, 0) : null
  return (
    <div className="hud">
      <span>
        <span className="dim">elapsed </span>
        <b className="cyan">{elapsed != null ? `${elapsed.toFixed(1)}s` : '—'}</b>
        {elapsed == null && r && <span className="dim"> (cached run)</span>}
        {running && <span className="amber"> ● live</span>}
      </span>
      <span>
        <span className="dim">api calls </span>
        <b className="cyan">{r ? r.api_calls : '—'}</b>
        <span className="dim"> logical · </span>
        <b className={r && r.upstream_calls === 0 ? 'green' : 'amber'}>{r ? r.upstream_calls : '—'}</b>
        <span className="dim"> upstream</span>
      </span>
      <span>
        <span className="dim">worker time </span>
        <b>{phaseTotal != null ? `${phaseTotal.toFixed(1)}s` : '—'}</b>
      </span>
      <span>
        <span className="dim">cost </span>
        <b className="green">₹0</b>
        <span className="dim"> free-tier providers</span>
      </span>
      {r?.partial && (
        <span className="tag amber" title={r.flags.filter((f) => f.startsWith('partial:')).join('\n')}>
          partial — degraded source, result stands on Postgres
        </span>
      )}
      {r && (
        <span className="dim mono" style={{ marginLeft: 'auto', fontSize: 12 }}>
          block {r.pins.snapshot_block} · {r.pins.label_set_version} · {r.pins.weight_hash}
        </span>
      )}
    </div>
  )
}
