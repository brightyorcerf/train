import { useEffect, useRef } from 'react'

// Mostly white on the sky blue, with the old accent vocabulary: gold (a crowned VASP), green
// (an exchange endpoint), coral (a sanctioned address).
const DOT_COLORS = ['#ffffff', '#ffffff', '#ffffff', '#ffffff', '#f5c451', '#3fa876', '#e8665a']

type Node = { x: number; y: number; vx: number; vy: number; r: number; color: string; flash: number }
type Pulse = { a: Node; b: Node; t: number; speed: number }

/** Ambient network for the hero — drifting wallet dots, thin links between near neighbours, and now
 *  and then a gold "payment" that runs along a link and lights the node it lands on. Pure canvas:
 *  this is atmosphere, not a graph, so it does not need cytoscape. The centre band is thinned so
 *  the headline stays legible. */
export function NetworkCanvas() {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = ref.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    const dpr = devicePixelRatio
    let w = 0, h = 0, raf = 0, last = performance.now(), nextPulse = 0
    const nodes: Node[] = []
    const pulses: Pulse[] = []
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches

    // how visible a point is: fades in a soft ellipse around the centred headline
    const vis = (x: number, y: number) => {
      const dx = (x - w / 2) / (w * 0.34), dy = (y - h * 0.48) / (h * 0.34)
      return Math.min(1, 0.22 + Math.max(0, Math.hypot(dx, dy) - 0.35) * 1.1)
    }

    function resize() {
      const p = canvas!.parentElement!
      w = canvas!.width = p.clientWidth * dpr
      h = canvas!.height = p.clientHeight * dpr
      canvas!.style.width = `${p.clientWidth}px`
      canvas!.style.height = `${p.clientHeight}px`
      const count = Math.round((w * h) / (21000 * dpr * dpr))
      nodes.length = 0
      pulses.length = 0
      for (let i = 0; i < count; i++) {
        const big = Math.random() < 0.08
        nodes.push({
          x: Math.random() * w, y: Math.random() * h,
          vx: (Math.random() - 0.5) * 0.14 * dpr, vy: (Math.random() - 0.5) * 0.14 * dpr,
          r: (big ? 3.4 + Math.random() * 1.6 : 1.3 + Math.random() * 1.9) * dpr,
          color: DOT_COLORS[Math.floor(Math.random() * DOT_COLORS.length)], flash: 0,
        })
      }
    }

    const maxDist = () => 150 * dpr

    function tick(now: number) {
      const dt = Math.min(50, now - last)
      last = now
      ctx!.clearRect(0, 0, w, h)
      if (!still) {
        for (const n of nodes) {
          n.x += n.vx; n.y += n.vy
          if (n.x < 0 || n.x > w) n.vx *= -1
          if (n.y < 0 || n.y > h) n.vy *= -1
          n.flash = Math.max(0, n.flash - dt / 900)
        }
      }
      const md = maxDist()
      const links: [Node, Node][] = []
      ctx!.lineWidth = dpr
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j]
          const d = Math.hypot(a.x - b.x, a.y - b.y)
          if (d < md) {
            links.push([a, b])
            const v = vis((a.x + b.x) / 2, (a.y + b.y) / 2)
            ctx!.strokeStyle = `rgba(255,255,255,${0.34 * (1 - d / md) * v})`
            ctx!.beginPath(); ctx!.moveTo(a.x, a.y); ctx!.lineTo(b.x, b.y); ctx!.stroke()
          }
        }
      }
      // a payment every ~0.7s along a random live link, drawn as a gold comet
      if (!still && now > nextPulse && links.length) {
        const [a, b] = links[Math.floor(Math.random() * links.length)]
        pulses.push(Math.random() < 0.5 ? { a, b, t: 0, speed: 0.0009 + Math.random() * 0.0006 }
          : { a: b, b: a, t: 0, speed: 0.0009 + Math.random() * 0.0006 })
        nextPulse = now + 500 + Math.random() * 500
      }
      for (let i = pulses.length - 1; i >= 0; i--) {
        const p = pulses[i]
        p.t += p.speed * dt
        if (p.t >= 1) { p.b.flash = 1; pulses.splice(i, 1); continue }
        const x = p.a.x + (p.b.x - p.a.x) * p.t, y = p.a.y + (p.b.y - p.a.y) * p.t
        const tx = p.a.x + (p.b.x - p.a.x) * Math.max(0, p.t - 0.18), ty = p.a.y + (p.b.y - p.a.y) * Math.max(0, p.t - 0.18)
        const v = vis(x, y)
        const trail = ctx!.createLinearGradient(tx, ty, x, y)
        trail.addColorStop(0, 'rgba(245,196,81,0)')
        trail.addColorStop(1, `rgba(255,214,110,${0.9 * v})`)
        ctx!.strokeStyle = trail
        ctx!.lineWidth = 2 * dpr
        ctx!.beginPath(); ctx!.moveTo(tx, ty); ctx!.lineTo(x, y); ctx!.stroke()
        const g = ctx!.createRadialGradient(x, y, 0, x, y, 7 * dpr)
        g.addColorStop(0, `rgba(255,230,150,${v})`)
        g.addColorStop(1, 'rgba(245,196,81,0)')
        ctx!.fillStyle = g
        ctx!.beginPath(); ctx!.arc(x, y, 7 * dpr, 0, Math.PI * 2); ctx!.fill()
      }
      for (const n of nodes) {
        const v = vis(n.x, n.y)
        if (n.flash > 0) {
          ctx!.fillStyle = `rgba(255,230,150,${0.55 * n.flash * v})`
          ctx!.beginPath(); ctx!.arc(n.x, n.y, n.r + 10 * dpr * n.flash, 0, Math.PI * 2); ctx!.fill()
        }
        if (n.r > 3.3 * dpr) {           // the few "wallets" carry a soft halo
          ctx!.fillStyle = `rgba(255,255,255,${0.16 * v})`
          ctx!.beginPath(); ctx!.arc(n.x, n.y, n.r * 2.6, 0, Math.PI * 2); ctx!.fill()
        }
        ctx!.globalAlpha = 0.35 + 0.65 * v
        ctx!.fillStyle = n.color
        ctx!.beginPath(); ctx!.arc(n.x, n.y, n.r, 0, Math.PI * 2); ctx!.fill()
        ctx!.globalAlpha = 1
      }
      raf = requestAnimationFrame(tick)
    }

    resize()
    raf = requestAnimationFrame(tick)
    window.addEventListener('resize', resize)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', resize) }
  }, [])

  return <canvas ref={ref} className="net" aria-hidden="true" />
}
