import { useState } from 'react'
import { useRevertOverride } from '../../api/admin-queries'
import { ApiError } from '../../api/client'

interface Props {
  overrideId: number
  label?: string  // default "Revert"
  onSuccess?: () => void
}

export function RevertButton({ overrideId, label = 'Revert', onSuccess }: Props) {
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mutation = useRevertOverride()

  async function doRevert() {
    setError(null)
    try {
      await mutation.mutateAsync({
        override_id: overrideId,
        body: { reason: 'Reverted via dashboard one-click button' },
      })
      setConfirming(false)
      onSuccess?.()
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : (e as Error).message)
    }
  }

  if (confirming) {
    return (
      <span className="inline-flex items-center gap-2">
        <button
          onClick={() => void doRevert()}
          disabled={mutation.isPending}
          className="px-2 py-0.5 rounded bg-amber-500/15 text-amber-400 border border-amber-500/30 hover:bg-amber-500/25 disabled:opacity-50 text-xs"
        >
          {mutation.isPending ? '…' : `Confirm ${label.toLowerCase()}?`}
        </button>
        <button
          onClick={() => setConfirming(false)}
          disabled={mutation.isPending}
          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          cancel
        </button>
        {error && <span className="text-xs text-red-400">{error}</span>}
      </span>
    )
  }

  return (
    <button
      onClick={() => setConfirming(true)}
      className="px-2 py-0.5 rounded bg-zinc-500/10 text-zinc-300 border border-zinc-500/30 hover:bg-zinc-500/20 text-xs"
    >
      ↺ {label}
    </button>
  )
}
