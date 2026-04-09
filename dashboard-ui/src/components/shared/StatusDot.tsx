interface StatusDotProps {
  status: 'green' | 'amber' | 'red' | 'gray'
  className?: string
}

const STATUS_COLORS: Record<StatusDotProps['status'], string> = {
  green: '#22c55e',
  amber: '#f59e0b',
  red: '#ef4444',
  gray: '#a1a1aa',
}

export function StatusDot({ status, className = '' }: StatusDotProps) {
  return (
    <span
      className={`inline-block rounded-full ${className}`}
      style={{ width: 6, height: 6, backgroundColor: STATUS_COLORS[status] }}
    />
  )
}
