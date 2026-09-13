// Typed fetch against the real backend (§14). Field names here are transcribed from live
// responses, not from the spec — the two differ (e.g. the engine returns `vasp_candidates`,
// and `wall_clock_s` is 0 for chord-driven traces because only the CLI sets it).
const API = '/api'

export type Pins = {
  snapshot_block: number
  label_set_version: string
  weight_hash?: string
  adapter_version?: string
  weights_frozen_at?: string
  max_calls?: number
}

export type Hop = { from: string; to: string; tx: string; ts: number; value: number; asset: string }

export type DepositEvent = { tx: string; ts: number; amount_btc?: number; amount?: number }

export type Nearest = {
  hops: number
  path: Hop[]
  endpoint: string
  role_basis: string
  deposit_event?: DepositEvent | null
}

export type Breakdown = {
  of: number
  factors: Record<string, number>
  weights: Record<string, number>
  contributions: Record<string, number>
  penalty: number
  weight_hash: string
  penalties_applied: string[]
}

export type Candidate = {
  entity: string
  entity_name: string
  score: number
  sahyog: string
  n_paths: number
  endpoints: string[]
  best_claim: string
  corroboration: number
  nearest: Nearest
  breakdown: Breakdown
}

export type TraceResult = {
  trace_id: string
  case_id: string
  wallet: string
  chain: string
  state: string
  recommended: string | null
  separation: string | null
  separation_pts: number | null
  rationale?: string
  reason?: string
  hops: number | null
  nearest: Nearest | null
  vasp_candidates: Candidate[]
  flags: string[]
  sweep_evidence: unknown[]
  pins: Pins
  api_calls: number
  upstream_calls: number
  store_hits: number
  addresses_seen: number
  same_owner_edges: number
  wall_clock_s: number
  phases: Record<string, number>
  deposit_event?: DepositEvent | null
  entity_sahyog?: string
  partial: boolean
}

export type Status = {
  state: string
  current_hop: number
  progress: number
  error: string | null
}

export type CaseRow = {
  case_id: string
  trace_id: string
  wallet: string
  chain: string
  source: string
  snapshot_block: number
  label_set_version: string
  created_at: string
  state: string
  current_hop: number
  progress: number
  finished_at: string | null
  result_state: string | null
  recommended: string | null
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`)
  if (!r.ok) throw new Error(`${r.status} ${(await r.json().catch(() => ({}))).detail ?? r.statusText}`)
  return r.json()
}

export const listCases = (limit = 50) => get<{ cases: CaseRow[] }>(`/cases?limit=${limit}`)
export const traceStatus = (id: string) => get<Status>(`/trace/${id}/status`)
export const traceResult = (id: string) => get<TraceResult>(`/trace/${id}`)
export const timeline = (id: string) => get<Record<string, unknown>>(`/trace/${id}/timeline`)
export const provenance = (id: string) => get<Record<string, unknown>>(`/trace/${id}/provenance`)

export async function createCase(body: {
  wallets: string[]
  chain: string
  snapshot_block?: number | null
  max_hops?: number
  fanout?: number
}) {
  const r = await fetch(`${API}/cases`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ fanout: 4, max_hops: 4, ...body }),
  })
  if (!r.ok) throw new Error(`${r.status} ${(await r.json().catch(() => ({}))).detail ?? r.statusText}`)
  return r.json() as Promise<{ trace_ids: string[]; case_ids: string[]; snapshot_block: number }>
}

/** Poll until every trace leaves the running states. onTick sees each poll, for the HUD. */
export async function waitAll(ids: string[], onTick: (s: Record<string, Status>) => void) {
  for (;;) {
    const entries = await Promise.all(ids.map(async (i) => [i, await traceStatus(i)] as const))
    const map = Object.fromEntries(entries)
    onTick(map)
    if (entries.every(([, s]) => s.state === 'DONE' || s.state === 'FAILED')) return map
    await new Promise((r) => setTimeout(r, 1500))
  }
}
