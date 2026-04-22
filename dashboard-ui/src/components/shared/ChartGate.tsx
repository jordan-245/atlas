import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** Tailwind class or inline style height for the wrapper. Must be non-empty. */
  className?: string
  style?: React.CSSProperties
}

/**
 * Defers rendering of its children (a recharts <ResponsiveContainer>) until the
 * wrapper div has non-zero dimensions. This silences the spurious recharts
 * "width(-1) and height(-1) of chart should be greater than 0" console.warn
 * that fires on the first paint before CSS has applied to the parent.
 *
 * Usage: replace `<div className="h-[260px]">...<ResponsiveContainer .../></div>`
 * with `<ChartGate className="h-[260px]">...<ResponsiveContainer ... /></ChartGate>`.
 */
export function ChartGate({ children, className, style }: Props) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [ready, setReady] = useState(false)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return

    // Fast path: if dimensions already available at layout time, mount immediately
    if (el.clientWidth > 0 && el.clientHeight > 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setReady(true)
      return
    }

    // Slow path: observe until the parent has measurable size
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect
      if (rect && rect.width > 0 && rect.height > 0) {
        setReady(true)
        observer.disconnect()
      }
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  return (
    <div ref={ref} className={className} style={style}>
      {ready ? children : null}
    </div>
  )
}
