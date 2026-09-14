import { useEffect, useRef } from 'react'

const DOT_COLORS = ['#ffffff', '#e8665a', '#3fa876', '#d9a521', '#ffffff', '#ffffff']

type Node = { x: number; y: number; vx: number; vy: number; r: number; color: string }

/** Ambient network backdrop for the hero — floating wallet nodes with thin connecting lines.
 *  Pure canvas, no deps: this is decoration, not a graph, so it doesn't need cytoscape. */
export function NetworkCanvas() {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let w = 0, h = 0, raf = 0
    const nodes: Node[] = []

    function resize() {
      const parent = canvas!.parentElement!
      w = canvas!.width = parent.clientWidth * devicePixelRatio
      h = canvas!.height = parent.clientHeight * devicePixelRatio
      canvas!.style.width = `${parent.clientWidth}px`
      canvas!.style.height = `${parent.clientHeight}px`
      const count = Math.round((w * h) / (28000 * devicePixelRatio * devicePixelRatio))
      nodes.length = 0
      for (let i = 0; i < count; i++) {
        nodes.push({
          x: Math.random() * w,
          y: Math.random() * h,
          vx: (Math.random() - 0.5) * 0.15 * devicePixelRatio,
          vy: (Math.random() - 0.5) * 0.15 * devicePixelRatio,
          r: (Math.random() * 2.5 + 2) * devicePixelRatio,
          color: DOT_COLORS[Math.floor(Math.random() * DOT_COLORS.length)],
        })
      }
    }

    function tick() {
      ctx!.clearRect(0, 0, w, h)
      for (const n of nodes) {
        n.x += n.vx; n.y += n.vy
        if (n.x < 0 || n.x > w) n.vx *= -1
        if (n.y < 0 || n.y > h) n.vy *= -1
      }
      const maxDist = 220 * devicePixelRatio
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j]
          const d = Math.hypot(a.x - b.x, a.y - b.y)
          if (d < maxDist) {
            ctx!.strokeStyle = `rgba(255,255,255,${0.38 * (1 - d / maxDist)})`
            ctx!.lineWidth = devicePixelRatio
            ctx!.beginPath()
            ctx!.moveTo(a.x, a.y)
            ctx!.lineTo(b.x, b.y)
            ctx!.stroke()
          }
        }
      }
      for (const n of nodes) {
        ctx!.fillStyle = n.color
        ctx!.beginPath()
        ctx!.arc(n.x, n.y, n.r, 0, Math.PI * 2)
        ctx!.fill()
      }
      raf = requestAnimationFrame(tick)
    }

    resize()
    tick()
    window.addEventListener('resize', resize)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', resize) }
  }, [])

  return <canvas ref={ref} aria-hidden="true" />
}
