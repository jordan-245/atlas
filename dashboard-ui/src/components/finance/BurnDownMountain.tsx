import { useMemo, useState, type MouseEvent } from 'react'
import type { PacePoint } from '../../api/types'
import {
  paceToBurnDown,
  daysInMonth,
  projectMonthEnd,
  buildDiffPolygons,
  fmtCcyShort,
  fmtSignedCcyShort,
  type BurnDownPoint,
} from './_burndown-math'

interface BurnDownMountainProps {
  paceData: PacePoint[]
  totalMonthlyBudget: number
  dailyAvg: number
  daysLeft?: number
  projectedTotal?: number
  paceStatus?: string
  paceDiff?: number
}

// ---------- viewBox constants ----------
const VB_W = 1180
const VB_H = 340
const PAD_LEFT = 64
const PAD_RIGHT = 88
const PAD_TOP = 28
const PAD_BOTTOM = 36
const INNER_W = VB_W - PAD_LEFT - PAD_RIGHT
const INNER_H = VB_H - PAD_TOP - PAD_BOTTOM

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

function monthNameFromIso(iso: string | undefined): string {
  if (!iso) return 'this month'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return 'this month'
  return MONTH_NAMES[d.getUTCMonth()] ?? 'this month'
}

function paceBadgeClass(status: string | undefined): string {
  if (status === 'under') return 'bg-[var(--color-green)]/15 text-[var(--color-green)] border-[var(--color-green)]/30'
  if (status === 'over') return 'bg-[var(--color-red)]/15 text-[var(--color-red)] border-[var(--color-red)]/30'
  return 'bg-[var(--color-surface-alt)] text-[var(--color-text-muted)] border-[var(--color-border)]'
}

// Linear interpolation across the point series for a given (possibly fractional) day.
function interpolate(points: BurnDownPoint[], day: number): number {
  if (points.length === 0) return 0
  if (day <= points[0].day) return points[0].actual
  for (let i = 1; i < points.length; i++) {
    if (day <= points[i].day) {
      const a = points[i - 1]
      const b = points[i]
      const span = b.day - a.day
      const t = span === 0 ? 0 : (day - a.day) / span
      return a.actual + t * (b.actual - a.actual)
    }
  }
  return points[points.length - 1].actual
}

export function BurnDownMountain({
  paceData,
  totalMonthlyBudget,
  dailyAvg,
  daysLeft,
  projectedTotal,
  paceStatus,
  paceDiff,
}: BurnDownMountainProps) {
  const [hoveredDay, setHoveredDay] = useState<number | null>(null)

  // ---------- empty / error state ----------
  const isEmpty = !paceData || paceData.length === 0 || totalMonthlyBudget <= 0

  // Memoise the geometry so we don't rebuild polygons on every hover.
  const geo = useMemo(() => {
    if (isEmpty) return null

    const points = paceToBurnDown(paceData)
    if (points.length === 0) return null

    const firstIso = paceData[0]?.date
    const totalDays = daysInMonth(firstIso)

    // 5% headroom so the line never touches the top edge when projected > budget.
    const yMax = Math.max(totalMonthlyBudget * 1.05, (projectedTotal ?? 0) * 1.05)

    const toX = (day: number): number =>
      PAD_LEFT + ((day - 1) / Math.max(1, totalDays - 1)) * INNER_W
    const toY = (dollars: number): number =>
      PAD_TOP + INNER_H - (dollars / yMax) * INNER_H

    // Anchor at day 1 = 0 if the data doesn't start there so the line starts at the origin.
    const anchored: BurnDownPoint[] = points[0].day > 1
      ? [{ day: 1, actual: 0, budget: 0 }, ...points]
      : points

    const projection = projectedTotal != null
      ? {
          projected: projectedTotal,
          lastDay: anchored[anchored.length - 1].day,
          overBudget: projectedTotal > totalMonthlyBudget,
          diff: projectedTotal - totalMonthlyBudget,
        }
      : projectMonthEnd(anchored, totalMonthlyBudget, dailyAvg, totalDays)

    const { greenCushion, redOverspend } = buildDiffPolygons(anchored, toX, toY)

    // Actual line polyline points
    const actualLinePts = anchored.map((p) => `${toX(p.day)},${toY(p.actual)}`).join(' ')

    return {
      points,
      anchored,
      totalDays,
      toX,
      toY,
      projection,
      greenCushion,
      redOverspend,
      actualLinePts,
      firstIso,
    }
  }, [paceData, totalMonthlyBudget, dailyAvg, projectedTotal, isEmpty])

  if (isEmpty || !geo) {
    return (
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5 dash-card">
        <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium mb-3">
          SPENDING BURN-DOWN
        </div>
        <div className="text-sm text-[var(--color-text-muted)] py-12 text-center">
          No spending pace data yet.
        </div>
      </div>
    )
  }

  const {
    anchored,
    totalDays,
    toX,
    toY,
    projection,
    greenCushion,
    redOverspend,
    actualLinePts,
    firstIso,
  } = geo

  const monthName = monthNameFromIso(firstIso)
  const lastPoint = anchored[anchored.length - 1]

  // Projection wedge color depends on whether projected total breaches budget.
  const projColor = projection.overBudget ? 'var(--color-amber)' : 'var(--color-green)'
  const projTextColor = projection.overBudget ? 'var(--color-red)' : 'var(--color-green)'

  // Y-axis tick fractions
  const yTicks = [0.25, 0.5, 0.75, 1.0]

  // X-axis ticks: every 5 days + the last day (avoid duplicates).
  const xTicks: number[] = []
  for (let d = 5; d < totalDays; d += 5) xTicks.push(d)
  xTicks.push(totalDays)
  if (!xTicks.includes(1)) xTicks.unshift(1)

  // Projection wedge: dashed continuation from last actual point.
  const projX1 = toX(lastPoint.day)
  const projY1 = toY(lastPoint.actual)
  const projX2 = toX(totalDays)
  const projY2 = toY(projection.projected)

  // End-of-month chip placement. Bias left of the right edge so it stays inside the SVG.
  const chipW = 96
  const chipH = 34
  const chipX = Math.min(projX2 - chipW + 12, VB_W - chipW - 4)
  const chipY = Math.max(PAD_TOP + 2, Math.min(projY2 - chipH - 12, PAD_TOP + INNER_H - chipH - 4))

  // Hover lookup
  const hover = hoveredDay != null
    ? (() => {
        const day = Math.max(1, Math.min(totalDays, hoveredDay))
        const actual = interpolate(anchored, day)
        const budget = ((day - 1) / Math.max(1, totalDays - 1)) * totalMonthlyBudget
        return { day, actual, budget, diff: actual - budget }
      })()
    : null

  // Mouse handlers: map clientX into viewBox day units.
  const onSvgMove = (ev: MouseEvent<SVGSVGElement>) => {
    const svg = ev.currentTarget
    const rect = svg.getBoundingClientRect()
    const px = ((ev.clientX - rect.left) / rect.width) * VB_W
    if (px < PAD_LEFT || px > PAD_LEFT + INNER_W) {
      setHoveredDay(null)
      return
    }
    const day = ((px - PAD_LEFT) / INNER_W) * Math.max(1, totalDays - 1) + 1
    setHoveredDay(Math.round(day))
  }

  const showPill = paceStatus != null && paceDiff != null
  const pillIsOver = paceStatus === 'over'
  const pillArrow = pillIsOver ? '▲' : '▼'
  const pillLabel = pillIsOver ? 'over' : 'under'

  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5 dash-card">
      {/* ---------- Header strip ---------- */}
      <div className="flex items-baseline justify-between gap-3 mb-4 flex-wrap">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium">
            SPENDING BURN-DOWN
          </div>
          <div className="text-base font-semibold text-[var(--color-text)] mt-1">
            Where will {monthName} land?
          </div>
        </div>
        {showPill ? (
          <div
            className={`rounded-full px-3 py-1 text-[11px] font-mono tabular-nums font-medium border ${paceBadgeClass(paceStatus)}`}
          >
            {pillArrow} {fmtCcyShort(Math.abs(paceDiff ?? 0))} {pillLabel}
          </div>
        ) : null}
      </div>

      {/* ---------- SVG wrapper: horizontal scroll on narrow viewports ---------- */}
      <div style={{ width: '100%', overflowX: 'auto' }}>
        <svg
          viewBox={`0 0 ${VB_W} ${VB_H}`}
          preserveAspectRatio="xMidYMid meet"
          role="img"
          aria-label={`${monthName} spending burn-down chart`}
          onMouseMove={onSvgMove}
          onMouseLeave={() => setHoveredDay(null)}
          style={{
            width: '100%',
            minWidth: 720,
            height: 'auto',
            aspectRatio: `${VB_W} / ${VB_H}`,
            display: 'block',
            cursor: 'crosshair',
          }}
        >
          {/* ===== Layer 1: faint grid ===== */}
          {yTicks.map((frac) => {
            const y = toY(totalMonthlyBudget * frac)
            return (
              <line
                key={`grid-${frac}`}
                x1={PAD_LEFT}
                y1={y}
                x2={PAD_LEFT + INNER_W}
                y2={y}
                stroke="var(--color-border)"
                strokeWidth={1}
                opacity={0.5}
              />
            )
          })}

          {/* ===== Layer 2: budget pace (straight diagonal, dashed) ===== */}
          <line
            x1={toX(1)}
            y1={toY(0)}
            x2={toX(totalDays)}
            y2={toY(totalMonthlyBudget)}
            stroke="var(--color-text-muted)"
            strokeWidth={1.5}
            strokeDasharray="4 4"
            fill="none"
          />

          {/* ===== Layer 3: green cushion polygon (actual < budget) ===== */}
          {greenCushion ? (
            <polygon
              points={greenCushion}
              fill="rgba(34, 197, 94, 0.16)"
              stroke="none"
            />
          ) : null}

          {/* ===== Layer 4: red overspend polygon (actual > budget) ===== */}
          {redOverspend ? (
            <polygon
              points={redOverspend}
              fill="rgba(239, 68, 68, 0.32)"
              stroke="none"
            />
          ) : null}

          {/* ===== Layer 5: actual spend line ===== */}
          <polyline
            points={actualLinePts}
            fill="none"
            stroke="var(--color-text)"
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
          />

          {/* ===== Layer 6: projection wedge ===== */}
          <line
            x1={projX1}
            y1={projY1}
            x2={projX2}
            y2={projY2}
            stroke={projColor}
            strokeWidth={2}
            strokeDasharray="6 5"
            fill="none"
          />
          <circle
            cx={projX2}
            cy={projY2}
            r={4}
            fill={projColor}
            stroke="var(--color-surface)"
            strokeWidth={1.5}
          />

          {/* ===== Layer 7: today marker (vertical dotted line at last actual day) ===== */}
          <line
            x1={toX(lastPoint.day)}
            y1={PAD_TOP}
            x2={toX(lastPoint.day)}
            y2={PAD_TOP + INNER_H}
            stroke="var(--color-text-muted)"
            strokeWidth={1}
            strokeDasharray="2 3"
            opacity={0.55}
          />
          <text
            x={toX(lastPoint.day)}
            y={PAD_TOP - 8}
            textAnchor="middle"
            fill="var(--color-text-muted)"
            fontSize={10}
            fontFamily="var(--font-mono, monospace)"
          >
            today · day {lastPoint.day}
          </text>

          {/* ===== Layer 8: Y-axis labels at 25/50/75/100% ===== */}
          {yTicks.map((frac) => {
            const v = totalMonthlyBudget * frac
            const y = toY(v)
            return (
              <text
                key={`ylab-${frac}`}
                x={PAD_LEFT - 10}
                y={y + 3}
                textAnchor="end"
                fill="var(--color-text-muted)"
                fontSize={10}
                fontFamily="var(--font-mono, monospace)"
              >
                {fmtCcyShort(v)}
              </text>
            )
          })}

          {/* ===== Layer 9: X-axis labels ===== */}
          {xTicks.map((d) => (
            <text
              key={`xlab-${d}`}
              x={toX(d)}
              y={PAD_TOP + INNER_H + 18}
              textAnchor="middle"
              fill="var(--color-text-muted)"
              fontSize={10}
              fontFamily="var(--font-mono, monospace)"
            >
              {d}
            </text>
          ))}
          <text
            x={PAD_LEFT + INNER_W / 2}
            y={PAD_TOP + INNER_H + 32}
            textAnchor="middle"
            fill="var(--color-text-muted)"
            fontSize={10}
            opacity={0.7}
          >
            day of month
          </text>

          {/* ===== Layer 10: end-of-month projection annotation ===== */}
          <g>
            <line
              x1={projX2}
              y1={projY2}
              x2={chipX + chipW}
              y2={chipY + chipH / 2}
              stroke={projColor}
              strokeWidth={1}
              strokeDasharray="2 2"
              opacity={0.55}
            />
            <rect
              x={chipX}
              y={chipY}
              width={chipW}
              height={chipH}
              rx={6}
              fill="var(--color-surface-alt)"
              stroke="var(--color-border)"
              strokeWidth={1}
            />
            <text
              x={chipX + 8}
              y={chipY + 13}
              fill="var(--color-text-muted)"
              fontSize={9}
              style={{ textTransform: 'uppercase', letterSpacing: 0.5 }}
            >
              projected
            </text>
            <text
              x={chipX + 8}
              y={chipY + 27}
              fill={projTextColor}
              fontSize={12}
              fontFamily="var(--font-mono, monospace)"
              fontWeight={600}
            >
              {fmtCcyShort(projection.projected)}
            </text>
          </g>

          {/* ===== Layer 11: budget end label ===== */}
          <text
            x={toX(totalDays) + 6}
            y={toY(totalMonthlyBudget) + 3}
            fill="var(--color-text-muted)"
            fontSize={10}
            fontFamily="var(--font-mono, monospace)"
          >
            budget
          </text>
          <text
            x={toX(totalDays) + 6}
            y={toY(totalMonthlyBudget) + 16}
            fill="var(--color-text-muted)"
            fontSize={10}
            opacity={0.7}
            fontFamily="var(--font-mono, monospace)"
          >
            {fmtCcyShort(totalMonthlyBudget)}
          </text>

          {/* ===== Layer 12: days-left footnote (top right) ===== */}
          {daysLeft != null ? (
            <text
              x={PAD_LEFT + INNER_W - 4}
              y={PAD_TOP + 14}
              textAnchor="end"
              fill="var(--color-text-muted)"
              fontSize={10}
              opacity={0.7}
            >
              {daysLeft} days left
            </text>
          ) : null}

          {/* ===== Layer 13: hover overlay ===== */}
          {hover ? (
            <g pointerEvents="none">
              <line
                x1={toX(hover.day)}
                y1={PAD_TOP}
                x2={toX(hover.day)}
                y2={PAD_TOP + INNER_H}
                stroke="var(--color-text-muted)"
                strokeWidth={1}
                strokeDasharray="2 3"
                opacity={0.7}
              />
              <circle
                cx={toX(hover.day)}
                cy={toY(hover.actual)}
                r={4}
                fill="var(--color-text)"
                stroke="var(--color-surface)"
                strokeWidth={2}
              />
              {(() => {
                // Position tooltip inside the chart, flipping sides if near the right edge.
                const tipW = 158
                const tipH = 64
                const baseX = toX(hover.day)
                const tipX = baseX + tipW + 12 > PAD_LEFT + INNER_W
                  ? baseX - tipW - 10
                  : baseX + 10
                const tipY = Math.max(PAD_TOP + 4, toY(hover.actual) - tipH - 8)
                const diffColor = hover.diff > 0 ? 'var(--color-red)' : 'var(--color-green)'
                return (
                  <g>
                    <rect
                      x={tipX}
                      y={tipY}
                      width={tipW}
                      height={tipH}
                      rx={6}
                      fill="var(--color-surface-alt)"
                      stroke="var(--color-border)"
                      strokeWidth={1}
                    />
                    <text
                      x={tipX + 9}
                      y={tipY + 14}
                      fill="var(--color-text-muted)"
                      fontSize={9}
                      style={{ textTransform: 'uppercase', letterSpacing: 0.5 }}
                    >
                      day {hover.day} of {totalDays}
                    </text>
                    <text
                      x={tipX + 9}
                      y={tipY + 29}
                      fill="var(--color-text)"
                      fontSize={11}
                      fontFamily="var(--font-mono, monospace)"
                    >
                      actual {fmtCcyShort(hover.actual)}
                    </text>
                    <text
                      x={tipX + 9}
                      y={tipY + 43}
                      fill="var(--color-text-muted)"
                      fontSize={11}
                      fontFamily="var(--font-mono, monospace)"
                    >
                      budget {fmtCcyShort(hover.budget)}
                    </text>
                    <text
                      x={tipX + 9}
                      y={tipY + 57}
                      fill={diffColor}
                      fontSize={11}
                      fontFamily="var(--font-mono, monospace)"
                      fontWeight={600}
                    >
                      {fmtSignedCcyShort(hover.diff)} {hover.diff > 0 ? 'over' : 'under'}
                    </text>
                  </g>
                )
              })()}
            </g>
          ) : null}
        </svg>
      </div>
    </div>
  )
}
