export function EmptyState({ message = 'No data available', className = '' }: { message?: string; className?: string }) {
  return <div className={`text-center py-8 text-sm text-[var(--color-text-muted)] ${className}`}>{message}</div>
}
