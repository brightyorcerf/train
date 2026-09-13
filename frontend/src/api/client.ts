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
  factor_from?: Record<string, string>
  penalty: number
  weight_hash: string
  penalties_applied: string[]
}

export type SweepEvidence = {
  address: string
  entity: string
  sweep: string
  share: number
  sweep_tx: string
  sweep_ts: number
  hot_wallet: string
  hot_label: string
  distinct_senders: number
  senders_truncated: boolean
  deposit_event?: DepositEvent | null
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
  sweep_evidence: SweepEvidence[]
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

/** `label_source` and `tier` are genuinely null on sweep-derived endpoints — the backend does not
 *  record them there, and the card says "not recorded" rather than inventing a value. */
export type ProvenanceRow = {
  entity: string
  entity_name: string | null
  sahyog: string | null
  endpoint: string | null
  role_basis: string | null
  hops: number | null
  label_source: string | null
  tier: string | null
  confidence_index: number | null
  n_paths: number | null
  deposit_event?: DepositEvent | null
}

export type ResponseHash = {
  request_key: string
  request: string
  content_hash: string
  provider: string
  fetched_at: string
}

export type Provenance = {
  trace_id: string
  wallet: string
  pins: Pins
  provenance: ProvenanceRow[]
  sweep_evidence: SweepEvidence[]
  flags: string[]
  response_hashes: ResponseHash[]
}

export type Rescore = {
  trace_id: string
  weights?: Record<string, number>
  weight_hash?: string
  frozen_weights?: Record<string, number>
  frozen_weight_hash?: string
  recommended: string | null
  base_recommended: string | null
  separation?: string | null
  separation_pts?: number | null
  ranked: { entity: string; entity_name: string | null; score: number }[]
  base_ranked?: string[]
  rank_unchanged: boolean
  stability?: { unchanged: number; n: number; pct: number }
  note: string
}

export type SharedNode = {
  address: string
  chain: string
  shared_by: number
  traces: string[]
  total_received: number
  total_sent: number
  asset: string
  min_hop: number
  txs: string[]
  is_traced_wallet: boolean
  labels: { role: string; entity: string; source: string; basis: string; confidence: number }[]
  role: string
  entity: string | null
  entity_name: string | null
  sahyog: string
  interpretation: string
}

export type Convergence = {
  trace_ids: string[]
  wallets: string[]
  shared_nodes: SharedNode[]
  n_shared: number
}

/** The subgraph the trace actually walked, from Postgres (§7.6) — so the centrepiece renders even
 *  with Neo4j down or rebuilding. `kind: 'tx'` nodes are the BTC :Tx hypernodes (§7.3); EVM has
 *  none, which is the visible data-model difference (§13). */
export type GraphNode = {
  id: string
  kind: 'address' | 'tx'
  hop: number
  chain: string
  label?: string
  role?: string
  entity?: string | null
  entity_name?: string | null
  sahyog?: string | null
  role_basis?: string | null
  boundary?: string | null
  is_wallet?: boolean
  crowned?: boolean
  score?: number | null
}

export type GraphEdge = {
  source: string
  target: string
  kind: string
  amount: number
  asset: string
  tx: string
  hop: number
}

export type TraceGraph = {
  trace_id: string
  chain: string
  wallet: string
  state: string
  partial: boolean
  max_hop: number
  nodes: GraphNode[]
  edges: GraphEdge[]
  truncated: boolean
  n_edges_total: number
  note: string
}

export type Onboarding = {
  target_vasp: string
  target_vasp_name: string
  target_vasp_sahyog: string
  jurisdiction: string[]
  routable_via_sahyog: boolean
  route: string
}

export type Disclosure = {
  request_id: string
  accepted: boolean
  note: string
  disclosure_payload: Record<string, unknown> & {
    target_vasp: string | null
    routable_via_sahyog: boolean
    route: string
    legal_basis?: string
    schema_note?: string
  }
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`)
  if (!r.ok) throw new Error(`${r.status} ${(await r.json().catch(() => ({}))).detail ?? r.statusText}`)
  return r.json()
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${r.status} ${(await r.json().catch(() => ({}))).detail ?? r.statusText}`)
  return r.json()
}

export const listCases = (limit = 50) => get<{ cases: CaseRow[] }>(`/cases?limit=${limit}`)
export const traceStatus = (id: string) => get<Status>(`/trace/${id}/status`)
export const traceResult = (id: string) => get<TraceResult>(`/trace/${id}`)
export const timeline = (id: string) => get<Record<string, unknown>>(`/trace/${id}/timeline`)
export const provenance = (id: string) => get<Provenance>(`/trace/${id}/provenance`)
export const traceGraph = (id: string) => get<TraceGraph>(`/trace/${id}/graph`)
export const onboarding = (entity: string) => get<Onboarding>(`/sahyog/onboarding/${entity}`)
export const disclosure = (id: string) => post<Disclosure>(`/sahyog/disclosure?trace_id=${id}`, {})

/** The report is a real PDF stream, so it is a link the browser opens — not a fetch. */
export const reportUrl = (id: string) => `${API}/report/${id}`

export const convergence = (ids: string[], chain: string, minShared = 2) =>
  get<Convergence>(`/convergence?trace_ids=${ids.join(',')}&chain=${chain}&min_shared=${minShared}`)

/** Re-rank a stored trace under another weight profile. The backend re-runs the same function the
 *  eval harness calls, so there is no second copy of the scoring arithmetic in this client. */
export const rescore = (id: string, body: {
  weights?: Record<string, number>
  perturb_pct?: number
  seed?: number
  profiles?: number
}) => post<Rescore>(`/trace/${id}/rescore`, body)

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

/** Poll until every trace leaves the running states. onTick sees each poll, for the HUD.
 *
 *  The backend now lands a dead chord in FAILED (its errback), so this loop normally ends on a real
 *  terminal state. The deadline is the backstop for the case where it does not — a worker killed
 *  before the errback runs would otherwise leave the console spinning forever, which is the worst
 *  thing that can happen on stage. Giving up loudly beats a spinner that never resolves. */
export async function waitAll(
  ids: string[],
  onTick: (s: Record<string, Status>) => void,
  timeoutMs = 600_000,
) {
  const t0 = Date.now()
  for (;;) {
    const entries = await Promise.all(ids.map(async (i) => [i, await traceStatus(i)] as const))
    const map = Object.fromEntries(entries)
    onTick(map)
    if (entries.every(([, s]) => s.state === 'DONE' || s.state === 'FAILED')) return map
    if (Date.now() - t0 > timeoutMs) {
      const stuck = entries.filter(([, s]) => s.state !== 'DONE' && s.state !== 'FAILED')
      throw new Error(
        `gave up after ${Math.round((Date.now() - t0) / 1000)}s — ` +
        stuck.map(([i, s]) => `${i.slice(0, 8)} still ${s.state} at hop ${s.current_hop}`).join(', ') +
        '. The job never reached a terminal state; check the worker log.',
      )
    }
    await new Promise((r) => setTimeout(r, 1500))
  }
}
