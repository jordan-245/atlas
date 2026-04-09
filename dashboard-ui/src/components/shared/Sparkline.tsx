import { Line, LineChart, ResponsiveContainer } from 'recharts'

interface SparklineProps {
  data: number[]
  color?: string
  height?: number
  strokeWidth?: number
}

export function Sparkline({ data, color, height = 32, strokeWidth = 1.5 }: SparklineProps) {
  if (!data || data.length === 0) {
    return <div style={{ height }} />
  }

  const resolvedColor =
    color ?? (data[data.length - 1] >= data[0] ? '#22c55e' : '#ef4444')

  const chartData = data.map((value, index) => ({ index, value }))

  return (
    <div style={{ height, width: '100%' }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData}>
          <Line
            type="monotone"
            dataKey="value"
            stroke={resolvedColor}
            strokeWidth={strokeWidth}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
