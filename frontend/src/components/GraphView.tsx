import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import cytoscape from 'cytoscape'
import type { Core, ElementDefinition } from 'cytoscape'
import { traceGraph } from '../api/client'
import type { TraceGraph, TraceResult } from '../api/client'

/** GraphView (§13) — the centrepiece, built last on purpose.
 *
 *  Three things this is actually arguing, none of them decoration:
 *
 *  1. THE TWO DATA MODELS ARE DIFFERENT AND YOU CAN SEE IT (§7.1-7.3). BTC renders the UTXO shape
 *     literally — rectangular :Tx hypernodes between circular addresses — while EVM renders
 *     address -> address arrows. A tool that flattened BTC to address->address would be hiding the
 *     part of the data model that is hard, so the hypernode stays on screen.
 *  2. THE REVEAL IS THE REAL BACKEND'S PROGRESS, not an animation. Hops appear in the order the
 *     chords actually completed them, keyed off each edge's recorded `hop`. It is a replay of work
 *     that happened, which is why it is allowed to look like a replay.
 *  3. BOUNDARIES ARE EVENTS, NOT DEAD ENDS (§9.3). A mixer is a red STOP wall, a bridge is a portal
 *     naming the chains it serves, a DEX is a swap glyph with the edge fading after it. The trace
 *     stopping somewhere is a finding; rendering it as a failure would misrepresent it.
 *
 *  Data comes from Postgres via /trace/{id}/graph, never Neo4j — so this still draws while the
 *  derived index is down or rebuilding (§7.6). That is a stage-safety property, not an accident.
 */

const COLORS = {
  bg: '#0b0f14', line: '#223040', dim: '#7d93a8', fg: '#d7e3ee',
  cyan: '#35d0e0', amber: '#e0a23a', red: '#e05260', gold: '#f0c860', green: '#46c08a',
}

// Role -> the colour the rest of the console already uses for that role, so the graph is not
// speaking a second visual language (ConvergencePanel uses the same mapping).
const ROLE_COLOR: Record<string, string> = {
  mixer: COLORS.red, sanctioned: COLORS.red, bridge: COLORS.amber, dex: COLORS.amber,
  deposit: COLORS.green, hot: COLORS.green, unlabeled: COLORS.dim,
}

const short = (a: string) => (a.length > 18 ? `${a.slice(0, 8)}…${a.slice(-5)}` : a)

const STYLE: cytoscape.StylesheetJson = [
  {
    selector: 'node',
    style: {
      'background-color': COLORS.line,
      'border-width': 1.5,
      'border-color': COLORS.dim,
      label: 'data(label)',
      color: COLORS.dim,
      'font-size': 9,
      'font-family': 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      'text-valign': 'bottom',
      'text-margin-y': 4,
      'text-wrap': 'wrap',
      width: 26,
      height: 26,
    },
  },
  // §7.3: a BTC transaction IS a node. Rectangular, so the hypernode reads as a different kind of
  // thing at a glance and not as "an address with a strange name".
  {
    selector: 'node[kind = "tx"]',
    style: {
      shape: 'round-rectangle',
      width: 34,
      height: 16,
      'background-color': '#172029',
      'border-color': COLORS.line,
      color: COLORS.dim,
      'font-size': 8,
    },
  },
  {
    selector: 'node[kind = "address"]',
    style: { shape: 'ellipse', 'background-color': 'data(fill)', 'border-color': 'data(stroke)' },
  },
  // Confidence as glow: the index drives shadow size, so a strong candidate is visibly hotter than
  // a weak one without printing a number the viewer would read as a probability (§11.1).
  {
    selector: 'node[glow > 0]',
    style: { 'shadow-blur': 'data(glow)', 'shadow-color': 'data(stroke)', 'shadow-opacity': 0.9 },
  },
  {
    selector: 'node[?isWallet]',
    style: {
      'border-color': COLORS.cyan, 'border-width': 2.5, width: 32, height: 32,
      color: COLORS.cyan, 'font-size': 10,
    },
  },
  // The crowned target: gold halo. One node on screen gets this, matching the one crowned VASP.
  {
    selector: 'node[?crowned]',
    style: {
      'border-color': COLORS.gold, 'border-width': 4, width: 40, height: 40,
      color: COLORS.gold, 'font-size': 11, 'font-weight': 'bold',
      'shadow-blur': 26, 'shadow-color': COLORS.gold, 'shadow-opacity': 0.95,
    },
  },
  // A mixer is a wall, not a node you might click through.
  {
    selector: 'node[role = "mixer"], node[role = "sanctioned"]',
    style: {
      shape: 'octagon', 'border-color': COLORS.red, 'border-width': 3,
      'background-color': 'rgba(224, 82, 96, 0.22)', color: COLORS.red, width: 40, height: 40,
    },
  },
  {
    selector: 'node[role = "bridge"]',
    style: {
      shape: 'diamond', 'border-color': COLORS.amber, 'border-width': 3,
      'background-color': 'rgba(224, 162, 58, 0.2)', color: COLORS.amber, width: 38, height: 38,
    },
  },
  {
    selector: 'node[role = "dex"]',
    style: {
      shape: 'hexagon', 'border-color': COLORS.amber, 'border-width': 2,
      'background-color': 'rgba(224, 162, 58, 0.14)', color: COLORS.amber,
    },
  },
  {
    selector: 'edge',
    style: {
      width: 'data(w)',
      'line-color': COLORS.line,
      'target-arrow-color': COLORS.line,
      'target-arrow-shape': 'triangle',
      'arrow-scale': 0.7,
      'curve-style': 'bezier',
      opacity: 0.75,
      label: 'data(label)',
      'font-size': 8,
      'font-family': 'ui-monospace, Menlo, monospace',
      color: COLORS.dim,
      'text-background-color': COLORS.bg,
      'text-background-opacity': 0.75,
      'text-background-padding': '2',
    },
  },
  // FUNDS/CREDITS are the two halves of one BTC transaction: no arrowhead into the tx node, so the
  // hypernode reads as a junction rather than a hop.
  { selector: 'edge[kind = "funds"]', style: { 'target-arrow-shape': 'none', 'line-style': 'solid' } },
  { selector: 'edge[?swapped]', style: { 'line-style': 'dashed', opacity: 0.4, 'line-color': COLORS.amber } },
  { selector: '.faded', style: { opacity: 0.12, 'text-opacity': 0 } },
]

type Props = { r: TraceResult }

export function GraphView({ r }: Props) {
  const box = useRef<HTMLDivElement>(null)
  const cy = useRef<Core | null>(null)
  const [g, setG] = useState<TraceGraph | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [hop, setHop] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [sel, setSel] = useState<Record<string, unknown> | null>(null)
  const [full, setFull] = useState(false)

  useEffect(() => {
    setG(null); setErr(null); setHop(0); setPlaying(false); setSel(null)
    traceGraph(r.trace_id).then((d) => { setG(d); setHop(d.max_hop) }).catch((e) => setErr(String(e)))
  }, [r.trace_id])

  /** The full subgraph is the honest payload and stays one click away — but a golden BTC case is
   *  246 nodes, almost all of them fan-out leaves, and drawing every one produces a flat smear that
   *  says nothing. The SPINE is what the case actually turns on: the suspect, the path to every
   *  ranked candidate, the service boundaries, and any labeled endpoint. The leaves are collapsed
   *  behind a count that names how many — never dropped silently, which would be the dishonest
   *  version of the same simplification. */
  const spine = useMemo(() => {
    const keep = new Set<string>()
    if (!g) return keep
    keep.add(g.wallet)
    for (const cand of r.vasp_candidates ?? []) {
      for (const h of cand.nearest?.path ?? []) {
        keep.add(h.from)
        keep.add(h.to)
        if (g.chain === 'btc' && h.tx) keep.add(`tx:${h.tx}`)
      }
      if (cand.nearest?.endpoint) keep.add(cand.nearest.endpoint)
    }
    for (const n of g.nodes) {
      if (n.boundary || n.crowned || n.is_wallet || (n.role && n.role !== 'unlabeled')) keep.add(n.id)
    }
    return keep
  }, [g, r])

  const elements = useMemo<ElementDefinition[]>(() => {
    if (!g) return []
    const swapped = new Set(
      g.nodes.filter((n) => n.role === 'dex').map((n) => n.id),
    )
    const shown = full ? g.nodes : g.nodes.filter((n) => spine.has(n.id))
    const vis = new Set(shown.map((n) => n.id))
    const shownEdges = g.edges.filter((e) => vis.has(e.source) && vis.has(e.target))
    const nodes = shown.map((n) => {
      const role = n.role ?? 'unlabeled'
      const stroke = n.crowned ? COLORS.gold : (ROLE_COLOR[role] ?? COLORS.dim)
      return {
        data: {
          id: n.id,
          kind: n.kind,
          role,
          hop: n.hop,
          isWallet: !!n.is_wallet,
          crowned: !!n.crowned,
          fill: n.kind === 'tx' ? '#172029' : 'rgba(23, 32, 41, 0.9)',
          stroke,
          // score is 0-100; keep the glow small so it reads as emphasis, not as a gauge
          glow: n.score ? Math.round(6 + (n.score / 100) * 20) : 0,
          label: n.kind === 'tx' ? `tx ${n.label}`
            : n.entity_name ? `${n.entity_name}${n.score ? ` ${n.score}` : ''}`
              : short(n.id),
          raw: n,
        },
      }
    })
    const edges = shownEdges.map((e, i) => ({
      data: {
        id: `e${i}`,
        source: e.source,
        target: e.target,
        kind: e.kind,
        hop: e.hop,
        w: Math.min(5, 0.8 + Math.log10(1 + Math.abs(e.amount)) * 1.3),
        swapped: swapped.has(e.source),
        label: e.amount ? `${e.amount} ${e.asset}` : '',
      },
    }))
    return [...nodes, ...edges]
  }, [g, full, spine])

  useEffect(() => {
    if (!box.current || !elements.length) return
    cy.current?.destroy()
    const c = cytoscape({
      container: box.current,
      elements,
      style: STYLE,
      layout: { name: 'breadthfirst', directed: true, spacingFactor: 1.15, padding: 24 },
      wheelSensitivity: 0.2,
    })
    c.on('tap', 'node', (ev) => setSel(ev.target.data('raw')))
    c.on('tap', (ev) => { if (ev.target === c) setSel(null) })
    cy.current = c
    return () => { c.destroy(); cy.current = null }
  }, [elements])

  // The staged reveal: everything past the current hop is faded, not removed, so the layout never
  // reflows under the viewer and the shape of the trace stays legible while it fills in.
  useEffect(() => {
    const c = cy.current
    if (!c) return
    c.batch(() => {
      c.elements().forEach((el) => {
        const h = el.data('hop') ?? 0
        el.toggleClass('faded', h > hop)
      })
    })
  }, [hop, elements])

  useEffect(() => {
    if (!playing || !g) return
    if (hop >= g.max_hop) { setPlaying(false); return }
    const t = setTimeout(() => setHop((h) => h + 1), 850)
    return () => clearTimeout(t)
  }, [playing, hop, g])

  const fit = useCallback(() => cy.current?.fit(undefined, 30), [])

  const boundaries = (g?.nodes ?? []).filter((n) => n.boundary)

  return (
    <div className="panel">
      <h2>Graph — the subgraph this trace actually walked</h2>

      {err && <div className="note err">{err}</div>}

      {!g && !err && <div className="note" style={{ marginTop: 0 }}>loading subgraph…</div>}

      {g && (
        <>
          <div className="row" style={{ marginBottom: 10 }}>
            <button onClick={() => { setHop(0); setPlaying(true) }} disabled={playing}>
              {playing ? 'revealing…' : 'replay hop by hop'}
            </button>
            <input
              type="range" min={0} max={g.max_hop} value={hop}
              onChange={(e) => { setPlaying(false); setHop(Number(e.target.value)) }}
              style={{ minWidth: 180, flex: '0 1 240px' }}
            />
            <span className="mono dim" style={{ fontSize: 12 }}>
              hop {hop} / {g.max_hop}
            </span>
            <button onClick={fit}>fit</button>
            <button onClick={() => setFull((f) => !f)}>
              {full ? 'spine only' : `show all ${g.nodes.length}`}
            </button>
            <span className="tag" style={{ color: COLORS.dim }}>
              {g.chain === 'btc' ? 'UTXO — ▭ tx hypernodes' : 'account — ● address → address'}
            </span>
            {g.partial && <span className="tag amber">partial</span>}
            {r.state === 'UNATTRIBUTED' && <span className="tag amber">UNATTRIBUTED</span>}
          </div>

          <div
            ref={box}
            style={{
              height: 420, background: COLORS.bg, border: `1px solid ${COLORS.line}`,
              borderRadius: 6,
            }}
          />

          <div className="row" style={{ marginTop: 8, gap: 14, fontSize: 11 }}>
            <span className="dim">legend</span>
            <span className="gold">◉ crowned target</span>
            <span className="cyan">◯ suspect wallet</span>
            <span className="red">⬣ mixer — STOP</span>
            <span className="amber">◆ bridge</span>
            <span className="amber">⬡ DEX — swap</span>
            <span className="green">● VASP endpoint</span>
          </div>

          {sel && (
            <div className="panel" style={{ margin: '10px 0 0', background: 'var(--panel-2)' }}>
              <div className="mono trunc" style={{ fontSize: 12 }}>{String(sel.id)}</div>
              <div className="note" style={{ marginTop: 4 }}>
                {sel.kind === 'tx' ? 'BTC transaction (:Tx hypernode, §7.3) — the junction that '
                  + 'joins inputs to outputs; it is a node because in the UTXO model it is one.'
                  : <>
                      role <b>{String(sel.role)}</b>
                      {sel.entity_name ? <> · {String(sel.entity_name)}</> : null}
                      {sel.role_basis ? <> · basis {String(sel.role_basis)}</> : null}
                      {sel.sahyog ? <> · sahyog {String(sel.sahyog)}</> : null}
                      {sel.score ? <> · index {String(sel.score)}/100</> : null}
                      {' '}· first seen at hop {String(sel.hop)}
                    </>}
              </div>
              {sel.boundary ? <div className="note amber">{String(sel.boundary)}</div> : null}
            </div>
          )}

          {boundaries.length > 0 && (
            <div className="note">
              <b>Trace boundaries (§9.3)</b> — the trace stopped here on purpose, and where it
              stopped is a finding, not a gap:
              {boundaries.map((b) => (
                <div key={b.id} className="mono" style={{ fontSize: 11, marginTop: 4 }}>
                  {b.boundary}
                </div>
              ))}
            </div>
          )}

          <div className="note">
            {full
              ? `Showing the complete walked subgraph — all ${g.nodes.length} nodes and
                 ${g.edges.length} edges.`
              : `Showing the spine: ${Math.min(spine.size, g.nodes.length)} of ${g.nodes.length} nodes —
                 the suspect, the path to every ranked candidate, each service boundary and every
                 labeled endpoint. The remaining ${Math.max(0, g.nodes.length - spine.size)} are
                 fan-out addresses the trace walked but nothing was concluded from; they are
                 collapsed for legibility, not withheld.`}
            {' '}
            {g.note}
            {g.truncated && ` Showing ${g.edges.length} of ${g.n_edges_total} edges — the largest
              subgraphs are capped for rendering, not for analysis; the ranking above used all of them.`}
          </div>
        </>
      )}
    </div>
  )
}
