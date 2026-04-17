interface ChartTooltipProps {
  active?: boolean
  payload?: Array<{ value?: number; name?: string; color?: string }>
  label?: string
  formatter?: (value: number, name: string) => string
  labelFormatter?: (label: string) => string
}

export function ChartTooltip({ active, payload, label, formatter, labelFormatter }: ChartTooltipProps) {
  if (!active || !payload?.length) return null
  return (
    <div className="dash-card !p-3 !shadow-lg text-sm" style={{ minWidth: 160 }}>
      <div className="text-[var(--color-text-muted)] text-xs mb-1.5">
        {labelFormatter ? labelFormatter(label ?? '') : label}
      </div>
      {payload.map((entry, i) => (
        <div key={i} className="flex justify-between gap-4">
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full" style={{ background: entry.color }} />
            {entry.name}
          </span>
          <span className="font-mono font-medium">
            {formatter ? formatter(entry.value ?? 0, entry.name ?? '') : entry.value?.toLocaleString()}
          </span>
        </div>
      ))}
    </div>
  )
}
