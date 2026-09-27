import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import cytoscape from 'cytoscape'
import type { Core, ElementDefinition } from 'cytoscape'
import { streamTrace, traceGraph } from '../api/client'
import type { GraphEdge, GraphNode, Technique, TraceGraph, TraceResult } from '../api/client'
import { short } from '../fmt'

/** GraphView (§13) — the investigation, told as the funds moved.
 *
 *  1. THE REVEAL IS THE REAL BACKEND'S WORK, not an animation of a guess. Every step reveals the
 *     elements whose recorded `hop` has been reached; the particles run only along the path the
 *     engine actually attributed (per-UTXO provenance, `nearest.path`). Nothing is drawn that the
 *     trace did not walk — the cinematic layer is emphasis over recorded data.
 *  2. THE TWO DATA MODELS ARE DIFFERENT AND YOU CAN SEE IT (§7.1-7.3): BTC keeps its :Tx
 *     hypernodes between addresses; EVM is address -> address.
 *  3. BOUNDARIES ARE EVENTS, NOT DEAD ENDS (§9.3): a mixer is a red wall, a bridge a portal, a DEX a
 *     swap glyph. A trace stopping is a finding, and the story ends on it rather than on silence.
 *
 *  LiveGraph (below) is the same stylesheet fed by GET /trace/{id}/stream while the Celery chords
 *  are still landing — the graph grows as the workers write it.
 */

const C = {
  bg: '#faf9f6', line: '#d8d3c4', line2: '#c3bdac', dim: '#8e95a5', fg: '#1a1f2e',
  accent: '#4aade8', accentDeep: '#2f8fc4', amber: '#e89e3a', red: '#e8665a', gold: '#d9a521', green: '#3fa876',
}
const ROLE_COLOR: Record<string, string> = {
  mixer: C.red, sanctioned: C.red, bridge: C.amber, dex: C.amber, deposit: C.green, hot: C.green, unlabeled: C.line2,
}
const STEP_MS = 2300
const BOUNDARY = ['coinjoin', 'service_hub', 'mixer', 'bridge']

const STYLE: cytoscape.StylesheetJson = [
  {
    selector: 'node',
    style: {
      'background-color': '#ffffff', 'border-width': 1.5, 'border-color': 'data(stroke)',
      label: 'data(label)', color: C.dim, 'font-size': 9, 'font-family': 'Departure Mono, ui-monospace, monospace',
      'text-valign': 'bottom', 'text-margin-y': 5, 'text-wrap': 'wrap', width: 18, height: 18,
      'transition-property': 'opacity, width, height, border-color, underlay-opacity, underlay-padding, text-opacity',
      'transition-duration': 0.6,
    },
  },
  { selector: 'node[kind = "tx"]', style: { shape: 'round-rectangle', width: 22, height: 10, 'background-color': '#efece2',
    'border-color': C.line2, 'border-width': 1, 'font-size': 8 } },
  { selector: 'node[!major][kind = "address"]', style: { width: 8, height: 8, 'border-width': 1 } },
  { selector: 'node[?major]', style: { width: 26, height: 26, color: C.fg } },
  { selector: 'node[glow > 0]', style: { 'underlay-color': 'data(stroke)', 'underlay-padding': 'data(glow)', 'underlay-opacity': 0.18,
    'underlay-shape': 'ellipse' } },
  { selector: 'node[?crowned]', style: { 'border-color': C.gold, 'border-width': 4, width: 44, height: 44, color: C.gold,
    'font-size': 12, 'font-weight': 'bold', 'background-color': '#fdf6e3', 'underlay-color': C.gold, 'underlay-padding': 14, 'underlay-shape': 'ellipse',
    'underlay-opacity': 0.35 } },
  { selector: 'node[role = "mixer"], node[role = "sanctioned"]', style: { shape: 'octagon', 'border-color': C.red, 'border-width': 3,
    'background-color': '#fdecea', color: C.red, width: 36, height: 36 } },
  { selector: 'node[role = "bridge"]', style: { shape: 'diamond', 'border-color': C.amber, 'border-width': 3,
    'background-color': '#fdf2e3', color: C.amber, width: 34, height: 34 } },
  { selector: 'node[role = "dex"]', style: { shape: 'hexagon', 'border-color': C.amber, 'border-width': 2,
    'background-color': '#fdf2e3', color: C.amber } },
  { selector: 'node[?isWallet]', style: { shape: 'ellipse', 'underlay-shape': 'ellipse', 'border-color': C.accent, 'border-width': 3, width: 34, height: 34, color: C.accent,
    'font-size': 11, 'background-color': '#eaf5fc', 'underlay-color': C.accent, 'underlay-padding': 10, 'underlay-opacity': 0.2 } },
  // a sanctioned suspect is still the suspect: keep its shape, flag it red
  { selector: 'node[?isWallet][role = "sanctioned"]', style: { 'border-color': C.red, 'underlay-color': C.red } },
  {
    selector: 'edge',
    style: {
      width: 'data(w)', 'line-color': C.line, 'target-arrow-color': C.line, 'target-arrow-shape': 'triangle',
      'arrow-scale': 0.6, 'curve-style': 'bezier', opacity: 0.55,
      'transition-property': 'opacity, line-color, width, target-arrow-color', 'transition-duration': 0.6,
    },
  },
  { selector: 'edge[kind = "funds"]', style: { 'target-arrow-shape': 'none' } },
  { selector: 'edge[?swapped]', style: { 'line-style': 'dashed', 'line-color': C.amber } },
  { selector: 'edge.path', style: { 'line-color': '#8cc9ee', 'target-arrow-color': '#8cc9ee', opacity: 0.95, width: 2.4,
    label: 'data(label)', 'font-size': 9, 'font-family': 'Departure Mono, monospace', color: C.fg,
    'text-background-color': C.bg, 'text-background-opacity': 0.85, 'text-background-padding': '3', 'text-rotation': 'autorotate' } },
  { selector: 'node.path', style: { color: C.fg, 'text-opacity': 1 } },
  { selector: 'node.current', style: { 'underlay-color': C.accent, 'underlay-opacity': 0.3, 'underlay-padding': 8, 'underlay-shape': 'ellipse' } },
  { selector: 'edge.current', style: { 'line-color': C.accent, 'target-arrow-color': C.accent, width: 3.2 } },
  { selector: 'node.pulse', style: { 'underlay-padding': 30, 'underlay-opacity': 0.12 } },
  { selector: '.faded', style: { opacity: 0.07, 'text-opacity': 0 } },
  { selector: 'node.new', style: { 'underlay-color': C.accent, 'underlay-opacity': 0.5, 'underlay-padding': 10 } },
]

type Built = { nodes: ElementDefinition[]; edges: ElementDefinition[] }

/** GraphNode/GraphEdge -> cytoscape elements. Shared by the replay and the live view. */
function build(nodes: GraphNode[], edges: GraphEdge[], pathNodes = new Set<string>(), pathEdges = new Set<string>()): Built {
  const dex = new Set(nodes.filter((n) => n.role === 'dex').map((n) => n.id))
  const seen = new Set<string>()
  return {
    nodes: nodes.map((n) => {
      const role = n.role ?? 'unlabeled'
      const major = !!(n.is_wallet || n.crowned || n.boundary || (n.role && n.role !== 'unlabeled') || pathNodes.has(n.id))
      return {
        data: {
          id: n.id, kind: n.kind, role, hop: n.hop, isWallet: !!n.is_wallet, crowned: !!n.crowned, major,
          stroke: n.crowned ? C.gold : (ROLE_COLOR[role] ?? C.line2),
          glow: n.score ? Math.round(6 + (n.score / 100) * 14) : 0,
          label: n.kind === 'tx' ? (pathNodes.has(n.id) ? `tx ${n.label?.slice(0, 8)}` : '')
            : n.entity_name ? `${n.entity_name}${n.score ? ` · ${n.score}` : ''}`
              : major ? short(n.id, 7, 4) : '',
          raw: n,
        },
        classes: pathNodes.has(n.id) ? 'path' : '',
      }
    }),
    edges: edges.flatMap((e) => {
      const id = `${e.source}>${e.target}>${e.kind}`
      if (seen.has(id)) return []
      seen.add(id)
      return [{
        data: {
          id, source: e.source, target: e.target, kind: e.kind, hop: e.hop,
          w: Math.min(4, 0.7 + Math.log10(1 + Math.abs(e.amount)) * 1.1),
          swapped: dex.has(e.source), label: e.amount ? `${+e.amount.toFixed(4)} ${e.asset}` : '',
        },
        classes: pathEdges.has(id) ? 'path' : '',
      }]
    }),
  }
}

type Step = {
  n: number
  reveal: number          // show elements with hop < reveal (wallet always)
  title: string
  meta: string
  say: string
  tone?: 'land' | 'stop'
  focus: string[]         // element ids to frame + highlight
  techs: Technique[]
}

/** Particles along the attributed path — requestAnimationFrame over a canvas laid on top of
 *  cytoscape, reading the edges' rendered endpoints every frame so pan/zoom stay in sync. */
function useParticles(cy: React.MutableRefObject<Core | null>, canvas: React.RefObject<HTMLCanvasElement | null>,
                      active: string[], current: string[]) {
  useEffect(() => {
    const el = canvas.current
    const ctx = el?.getContext('2d')
    if (!el || !ctx) return
    let raf = 0
    const t0 = performance.now()
    const cur = new Set(current)
    const frame = (t: number) => {
      const dpr = devicePixelRatio
      const w = el.clientWidth, h = el.clientHeight
      if (el.width !== w * dpr) { el.width = w * dpr; el.height = h * dpr }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, w, h)
      const c = cy.current
      if (c) {
        for (const id of active) {
          const e = c.getElementById(id)
          if (!e.length || e.hasClass('faded')) continue
          const a = e.renderedSourceEndpoint(), b = e.renderedTargetEndpoint()
          const len = Math.hypot(b.x - a.x, b.y - a.y)
          const hot = cur.has(id)
          const n = Math.max(2, Math.round(len / (hot ? 26 : 44)))
          for (let i = 0; i < n; i++) {
            const p = (((t - t0) / (hot ? 900 : 1600)) + i / n) % 1
            const x = a.x + (b.x - a.x) * p, y = a.y + (b.y - a.y) * p
            const r = hot ? 2.6 : 1.8
            const g = ctx.createRadialGradient(x, y, 0, x, y, r * 4)
            g.addColorStop(0, hot ? 'rgba(217,165,33,0.95)' : 'rgba(47,143,196,0.85)')
            g.addColorStop(1, hot ? 'rgba(217,165,33,0)' : 'rgba(74,173,232,0)')
            ctx.fillStyle = g
            ctx.beginPath(); ctx.arc(x, y, r * 4, 0, Math.PI * 2); ctx.fill()
          }
        }
      }
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)
    return () => cancelAnimationFrame(raf)
  }, [cy, canvas, active, current])
}

export function GraphView({ r, techs, autoplay = true }: { r: TraceResult; techs: Technique[]; autoplay?: boolean }) {
  const box = useRef<HTMLDivElement>(null)
  const fx = useRef<HTMLCanvasElement>(null)
  const cy = useRef<Core | null>(null)
  const [g, setG] = useState<TraceGraph | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [step, setStep] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [full, setFull] = useState(false)
  const [sel, setSel] = useState<GraphNode | null>(null)

  useEffect(() => {
    setG(null); setErr(null); setSel(null); setStep(0); setPlaying(false)
    traceGraph(r.trace_id).then(setG).catch((e) => setErr(String(e)))
  }, [r.trace_id])

  const top = (r.vasp_candidates ?? []).find((c) => c.entity === r.recommended) ?? r.vasp_candidates?.[0]
  const path = useMemo(() => (top?.nearest ?? r.nearest)?.path ?? [], [top, r.nearest])
  const btc = r.chain === 'btc'

  /** Element ids along the attributed path, per path hop (1-based), in the graph's own id scheme. */
  const pathIds = useMemo(() => path.map((h) => btc
    ? { nodes: [h.from, `tx:${h.tx}`, h.to], edges: [`${h.from}>tx:${h.tx}>funds`, `tx:${h.tx}>${h.to}>credits`] }
    : { nodes: [h.from, h.to], edges: [`${h.from}>${h.to}>native`, `${h.from}>${h.to}>erc20`, `${h.from}>${h.to}>internal`] }),
  [path, btc])

  /** The spine: suspect, every ranked candidate's path, boundaries, labeled endpoints. The leaves
   *  are collapsed behind a count ("show all N") — never dropped silently. */
  const spine = useMemo(() => {
    const keep = new Set<string>()
    if (!g) return keep
    keep.add(g.wallet)
    for (const cand of r.vasp_candidates ?? []) {
      for (const h of cand.nearest?.path ?? []) {
        keep.add(h.from); keep.add(h.to)
        if (btc && h.tx) keep.add(`tx:${h.tx}`)
      }
    }
    for (const n of g.nodes) if (n.boundary || n.crowned || n.is_wallet || n.score) keep.add(n.id)
    // one level of context around the attributed path, so a consolidation reads as one
    const onPath = new Set(pathIds.flatMap((p) => p.nodes))
    for (const e of g.edges) {
      if (e.kind === 'funds' && onPath.has(e.target)) keep.add(e.source)
      if (e.kind === 'credits' && onPath.has(e.source)) keep.add(e.target)
    }
    return keep
  }, [g, r, btc, pathIds])

  const elements = useMemo(() => {
    if (!g) return null
    const shown = full ? g.nodes : g.nodes.filter((n) => spine.has(n.id))
    const vis = new Set(shown.map((n) => n.id))
    const b = build(shown, g.edges.filter((e) => vis.has(e.source) && vis.has(e.target)),
      new Set(pathIds.flatMap((p) => p.nodes)), new Set(pathIds.flatMap((p) => p.edges)))
    return [...b.nodes, ...b.edges]
  }, [g, full, spine, pathIds])

  const steps = useMemo<Step[]>(() => {
    if (!g) return []
    const at = (h: number) => techs.filter((t) => t.hop === h)
    const out: Step[] = [{
      n: 0, reveal: 0, title: 'Suspect wallet', meta: r.wallet, focus: [r.wallet],
      say: `Start at the suspect wallet ${short(r.wallet, 7, 4)}`, techs: at(0),
    }]
    if (path.length) {
      path.forEach((h, i) => {
        const t = at(i + 1).filter((x) => x.kind !== 'deposit_sweep')
        out.push({
          n: i + 1, reveal: i + 1,
          title: `Hop ${i + 1} · ${+h.value.toFixed(6)} ${h.asset}`,
          meta: `${short(h.from, 7, 4)} → ${short(h.to, 7, 4)} · tx ${h.tx.slice(0, 10)}… · ${new Date(h.ts * 1000).toISOString().slice(0, 16).replace('T', ' ')}`,
          say: t.length ? `${t[0].title}: ${t[0].detail.split('; ')[0]}` : `${+h.value.toFixed(6)} ${h.asset} moves to ${short(h.to, 7, 4)}`,
          focus: [...pathIds[i].nodes, ...pathIds[i].edges], techs: t,
        })
      })
      const sweep = techs.find((t) => t.kind === 'deposit_sweep')
      const name = top?.entity_name ?? top?.entity
      const rival = r.vasp_candidates?.[1]
      out.push(r.recommended ? {
        n: path.length + 1, reveal: g.max_hop + 1, tone: 'land',
        title: `Landed · ${name}`,
        meta: `${path[path.length - 1].to} · ${top?.nearest?.role_basis ?? ''}`,
        say: sweep ? `Landed at a ${name} deposit address: ${sweep.detail.split('; ')[0]}`
          : `Landed at ${name} (${top?.nearest?.role_basis})`,
        focus: [path[path.length - 1].to], techs: sweep ? [sweep] : [],
      } : {
        // reached an exchange, but the evidence did not separate it from the runner-up
        n: path.length + 1, reveal: g.max_hop + 1, tone: 'stop',
        title: rival ? 'Contested: no target crowned' : 'Below the crown floor',
        meta: rival ? `${name} ${top?.score} vs ${rival.entity_name} ${rival.score} · gap ${r.separation_pts ?? 'n/a'} pts` : `${name} ${top?.score}/100`,
        say: rival ? `${name} (${top?.score}) and ${rival.entity_name} (${rival.score}) are too close to call, so it abstains`
          : `${name} reached, but at ${top?.score}/100 the evidence is too weak to name it`,
        focus: [path[path.length - 1].to, ...(rival?.nearest?.endpoint ? [rival.nearest.endpoint] : [])],
        techs: sweep ? [sweep] : [],
      })
    } else {
      for (let h = 1; h <= g.max_hop + 1; h++) {
        const t = at(h)
        const last = h === g.max_hop + 1
        out.push({
          n: h, reveal: h, tone: last ? 'stop' : undefined,
          title: last ? 'Trace stopped' : `Hop ${h}`,
          meta: last ? (r.reason ?? r.state) : `${g.nodes.filter((x) => x.hop === h).length} addresses reached`,
          say: last ? `Stopped: ${techs.find((x) => BOUNDARY.includes(x.kind))?.title ?? r.reason ?? 'no further outgoing value'}. No target crowned`
            : t.length ? `${t[0].title}: ${t[0].detail.split('; ')[0]}` : `The walk fans out to hop ${h}`,
          focus: g.nodes.filter((x) => x.boundary && x.hop <= h).map((x) => x.id), techs: t,
        })
      }
    }
    return out
  }, [g, r, path, pathIds, techs, top])

  // mount cytoscape once per element set
  useEffect(() => {
    if (!box.current || !elements) return
    const c = cytoscape({
      container: box.current, elements, style: STYLE,
      layout: { name: 'breadthfirst', directed: true, spacingFactor: 1.05, padding: 60,
        roots: elements.filter((e) => e.data.isWallet).map((e) => e.data.id as string) },
      wheelSensitivity: 0.2, maxZoom: 1.25, minZoom: 0.05,
    })
    c.on('tap', 'node', (ev) => setSel(ev.target.data('raw')))
    c.on('tap', (ev) => { if (ev.target === c) setSel(null) })
    cy.current = c
    return () => { c.destroy(); cy.current = null }
  }, [elements])

  // autoplay once per trace
  useEffect(() => {
    if (!elements || !steps.length) return
    if (autoplay) { setStep(0); setPlaying(true) } else setStep(steps.length - 1)
  }, [elements, steps.length, autoplay])

  const cur = steps[step]

  // apply a step: reveal by hop, highlight, frame the camera
  useEffect(() => {
    const c = cy.current
    if (!c || !cur) return
    const final = step === steps.length - 1
    // The attributed path is revealed by its own step order; everything else by its recorded hop.
    // (An edge keeps the hop at which the walk FIRST saw it, which can differ from its path step.)
    const past = new Set(pathIds.slice(0, cur.reveal).flatMap((p) => [...p.nodes, ...p.edges]))
    const later = new Set(pathIds.slice(cur.reveal).flatMap((p) => [...p.nodes, ...p.edges]))
    c.batch(() => {
      c.elements().forEach((el) => {
        const h = el.data('hop') ?? 0
        const id = el.id()
        const on = final || el.data('isWallet') || past.has(id) || (!later.has(id) && h < cur.reveal)
        el.toggleClass('faded', !on)
      })
      c.elements().removeClass('current pulse')
      for (const id of cur.focus) c.getElementById(id).addClass('current')
    })
    const focusEles = c.collection(cur.focus.map((id) => c.getElementById(id)).filter((e) => e.length) as never)
    const frame = final ? c.elements().not('.faded') : focusEles.union(focusEles.neighborhood().not('.faded'))
    // fit, then lift the frame so the caption strip at the bottom never covers the focus
    if (frame.length) {
      c.stop()
      const pad = final ? 80 : 150
      const bb = frame.boundingBox()
      const zoom = Math.min(c.maxZoom(), (c.width() - 2 * pad) / Math.max(1, bb.w), (c.height() - 2 * pad - 70) / Math.max(1, bb.h))
      c.animate({ zoom, pan: { x: c.width() / 2 - zoom * (bb.x1 + bb.w / 2), y: (c.height() - 70) / 2 - zoom * (bb.y1 + bb.h / 2) + 10 } },
        { duration: 900, easing: 'ease-in-out-cubic' })
    }
    if (cur.tone === 'land') {
      const n = c.getElementById(cur.focus[0])
      let on = false
      const t = window.setInterval(() => { on = !on; n.toggleClass('pulse', on) }, 700)
      return () => window.clearInterval(t)
    }
  }, [cur, step, steps.length, pathIds])

  useEffect(() => {
    if (!playing) return
    if (step >= steps.length - 1) { setPlaying(false); return }
    const t = window.setTimeout(() => setStep((s) => s + 1), step === 0 ? 1400 : STEP_MS)
    return () => window.clearTimeout(t)
  }, [playing, step, steps.length])

  const activeEdges = useMemo(() => pathIds.slice(0, Math.max(0, (cur?.reveal ?? 0))).flatMap((p) => p.edges), [pathIds, cur])
  const hotEdges = useMemo(() => (cur ? cur.focus.filter((id) => id.includes('>')) : []), [cur])
  useParticles(cy, fx, activeEdges, hotEdges)

  const jump = useCallback((i: number) => { setPlaying(false); setStep(i) }, [])

  return (
    <section aria-label="investigation">
      <div className="section-h"><span className="idx">02</span> How the money moved
        <span className="dim" style={{ fontSize: 12 }}>
          {g ? `${g.nodes.length} nodes · ${g.n_edges_total} edges walked · rendered from Postgres` : ''}
        </span>
      </div>
      {err && <div className="panel err">{err}</div>}
      <div className="investigate">
        <div>
          <div className="stage">
            {!g && !err && <div className="skeleton" style={{ position: 'absolute', inset: 12 }} />}
            <div ref={box} className="cy" />
            <canvas ref={fx} className="fx" />
            {g && (
              <div className="toolbar">
                <button className={playing ? '' : 'primary'} onClick={() => {
                  if (playing) setPlaying(false)
                  else { if (step >= steps.length - 1) setStep(0); setPlaying(true) }
                }}>{playing ? 'Pause' : step >= steps.length - 1 ? 'Replay the trace' : 'Play'}</button>
                <button onClick={() => jump(steps.length - 1)} disabled={step >= steps.length - 1}>Skip to end</button>
                <span className="spacer" />
                <button onClick={() => setFull((f) => !f)}>{full ? 'Spine only' : `Show all ${g.nodes.length}`}</button>
                <button onClick={() => cy.current?.animate({ fit: { eles: cy.current.elements(), padding: 40 } }, { duration: 500 })}>Fit</button>
                <span className="tag dim mono">{btc ? 'UTXO · ▭ tx hypernodes' : 'account · address → address'}</span>
              </div>
            )}
            {cur && (
              <div className="caption" key={step}>
                <span className="hopline">{cur.n === 0 ? 'START' : cur.tone ? cur.title.toUpperCase() : `HOP ${cur.n} / ${steps.length - 2}`}</span>
                <span className={`say ${cur.tone ?? ''}`}>{cur.say}</span>
              </div>
            )}
            <div className="scrub"><span style={{ width: `${steps.length > 1 ? (step / (steps.length - 1)) * 100 : 0}%` }} /></div>
          </div>
          <div className="legend">
            <span className="gold">◉ crowned VASP</span><span className="accent">◎ suspect</span>
            <span className="red">⬣ mixer · stop</span><span className="amber">◆ bridge · ⬡ DEX</span>
            <span className="green">● VASP endpoint</span><span>▭ transaction</span>
            <span style={{ marginLeft: 'auto' }}>particles run only along the attributed path</span>
          </div>
          {sel && (
            <div className="inset" style={{ margin: 12 }}>
              <div className="mono trunc" style={{ fontSize: 12 }}>{sel.id}</div>
              <div className="note" style={{ marginTop: 4 }}>
                {sel.kind === 'tx' ? 'BTC transaction: a :Tx hypernode joining inputs to outputs (§7.3).'
                  : <>role <b>{sel.role}</b>{sel.entity_name && <> · {sel.entity_name}</>}{sel.role_basis && <> · basis {sel.role_basis}</>}
                    {sel.sahyog && <> · sahyog {sel.sahyog}</>}{sel.score ? <> · index {sel.score}/100</> : null} · first seen at hop {sel.hop}</>}
              </div>
              {sel.boundary && <div className="note amber">{sel.boundary}</div>}
            </div>
          )}
        </div>
        <aside className="story" aria-label="storyboard">
          <header><span>Evidence, step by step</span><span className="dim mono" style={{ fontSize: 11 }}>{techs.length} findings</span></header>
          <ol>
            {steps.map((s, i) => (
              <li key={i} data-n={s.n} className={`step ${i === step ? 'on' : ''} ${i > step ? 'future' : ''} ${s.tone === 'land' ? 'land' : ''}`}
                  onClick={() => jump(i)}>
                <h4 className={s.tone === 'land' ? 'gold' : s.tone === 'stop' ? 'amber' : ''}>{s.title}</h4>
                <div className="meta">{s.meta}</div>
                {s.techs.length > 0 && (
                  <div className="tech">
                    {s.techs.map((t, j) => (
                      <div key={j}><b>{t.title}</b><span className={`basis ${t.basis}`}>{t.basis}</span><br />{t.detail}</div>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ol>
        </aside>
      </div>
      {g?.truncated && <div className="note">Showing {g.edges.length} of {g.n_edges_total} edges, capped for rendering, not for analysis; the ranking used all of them.</div>}
    </section>
  )
}

/** The graph as the workers write it: GET /trace/{id}/stream (SSE) adds each hop's nodes and edges
 *  the moment its chord tasks commit them to Postgres.
 *
 *  Drawn on a plain canvas in hop LANES, not with cytoscape: a full walk streams 2k+ nodes, and every
 *  graph layout tried turned that into an unreadable ring or line. Lanes keep the one thing a live
 *  view has to say legible — which hop the workers are on and how wide it fanned out. */
export function LiveGraph({ traceId, wallet }: { traceId: string; wallet: string }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const [st, setSt] = useState<{ state: string; hop: number; progress: number | null }>({ state: 'QUEUED', hop: 0, progress: 0 })
  const [count, setCount] = useState({ n: 0, e: 0, lanes: [] as number[] })

  useEffect(() => {
    const el = ref.current
    const ctx = el?.getContext('2d')
    if (!el || !ctx) return
    type P = { hop: number; tx: boolean; born: number; x: number; y: number; wallet: boolean }
    const nodes = new Map<string, P>()
    const edges: [string, string][] = []
    const lanes: string[][] = []
    const place = () => {
      const w = el.clientWidth, h = el.clientHeight, L = Math.max(lanes.length, 2)
      lanes.forEach((ids, hop) => {
        const x0 = 60 + (hop * (w - 120)) / (L - 1)
        const rows = Math.max(1, Math.floor((h - 60) / 7))
        const cols = Math.ceil(ids.length / rows)
        ids.forEach((id, i) => {
          const n = nodes.get(id)!
          const col = Math.floor(i / rows), row = i % rows
          const perCol = Math.min(rows, ids.length - col * rows)
          n.x = x0 + (col - (cols - 1) / 2) * 7
          n.y = h / 2 + (row - (perCol - 1) / 2) * 7
        })
      })
    }
    let raf = 0
    const frame = (t: number) => {
      const dpr = devicePixelRatio, w = el.clientWidth, h = el.clientHeight
      if (el.width !== w * dpr) { el.width = w * dpr; el.height = h * dpr; place() }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, w, h)
      ctx.lineWidth = 0.6
      for (const [a, b] of edges) {
        const p = nodes.get(a), q = nodes.get(b)
        if (!p || !q) continue
        const fresh = Math.max(0, 1 - (t - Math.max(p.born, q.born)) / 1200)
        ctx.strokeStyle = `rgba(47,143,196,${0.07 + fresh * 0.4})`
        ctx.beginPath(); ctx.moveTo(p.x, p.y)
        ctx.bezierCurveTo((p.x + q.x) / 2, p.y, (p.x + q.x) / 2, q.y, q.x, q.y); ctx.stroke()
      }
      for (const n of nodes.values()) {
        const fresh = Math.max(0, 1 - (t - n.born) / 900)
        if (fresh > 0) {
          ctx.fillStyle = `rgba(74,173,232,${fresh * 0.35})`
          ctx.beginPath(); ctx.arc(n.x, n.y, 3 + fresh * 6, 0, Math.PI * 2); ctx.fill()
        }
        ctx.fillStyle = n.wallet ? C.accentDeep : n.tx ? '#c3bdac' : fresh > 0 ? C.accent : '#8e95a5'
        const r = n.wallet ? 6 : n.tx ? 1.6 : 2.2
        if (n.tx) ctx.fillRect(n.x - r, n.y - r / 2, r * 2, r); else { ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.fill() }
      }
      lanes.forEach((ids, hop) => {
        const x = nodes.get(ids[0])?.x ?? 0
        ctx.fillStyle = C.dim; ctx.font = '11px Departure Mono, monospace'; ctx.textAlign = 'center'
        ctx.fillText(hop === 0 ? 'suspect' : `hop ${hop} · ${ids.length}`, x, 20)
      })
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)
    const stop = streamTrace(traceId, {
      edges: (d) => {
        const now = performance.now()
        for (const n of d.nodes) {
          if (nodes.has(n.id)) continue
          // the suspect is lane 0; everything else lands one lane past the hop that expanded it
          const isW = n.id === wallet
          const hop = isW ? 0 : n.hop + 1
          nodes.set(n.id, { hop, tx: n.kind === 'tx', born: now, x: 0, y: 0, wallet: isW })
          ;(lanes[hop] ??= []).push(n.id)
        }
        for (let i = 0; i < lanes.length; i++) lanes[i] ??= []
        for (const e of d.edges) edges.push([e.source, e.target])
        place()
        setCount({ n: nodes.size, e: edges.length, lanes: lanes.map((l) => l.length) })
      },
      status: (x) => setSt(x),
      done: () => undefined,
    })
    return () => { stop(); cancelAnimationFrame(raf) }
  }, [traceId, wallet])

  return (
    <section aria-label="live trace">
      <div className="section-h"><span className="idx">live</span> Tracing
        <span className="live-badge">{st.state === 'SCORING' ? 'scoring candidates' : `hop ${st.hop} · workers landing`}</span>
        <span className="dim mono" style={{ fontSize: 12 }}>{count.n} nodes · {count.e} edges streamed</span>
      </div>
      <div className="investigate" style={{ gridTemplateColumns: '1fr' }}>
        <div className="stage" style={{ minHeight: 440 }}>
          <canvas ref={ref} className="fx" style={{ width: '100%', height: '100%' }} />
          <div className="caption"><span className="hopline">SSE · /trace/{traceId.slice(0, 8)}/stream · one Celery chord per hop · edges appear as workers commit them</span></div>
          <div className="scrub"><span style={{ width: `${Math.round((st.progress ?? 0) * 100)}%` }} /></div>
        </div>
      </div>
    </section>
  )
}
