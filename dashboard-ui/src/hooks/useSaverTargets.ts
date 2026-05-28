/**
 * useSaverTargets -- per-saver goal targets persisted in localStorage.
 *
 * The /api/finance backend (Up Bank sync) doesn't model named saver goals
 * with targets/ETAs, so the dashboard owns these client-side until/if the
 * backend grows a `saver_goals` table. Each saver account (by display name)
 * gets an optional target dollar value; the UI derives % and ETA from
 * `balance / target` plus the user's average monthly net savings.
 *
 * Shape: `{ [accountName]: { target?: number } }`
 *
 * Cross-tab updates are propagated via the native `storage` event plus a
 * same-tab CustomEvent (`storage` only fires for OTHER tabs in modern browsers).
 */
import { useSyncExternalStore } from 'react'

const STORAGE_KEY = 'atlas:saver-targets:v1'
const SAME_TAB_EVENT = 'atlas:saver-targets:update'

export interface SaverTarget {
  target?: number
}

export type SaverTargetsMap = Record<string, SaverTarget>

function readRaw(): SaverTargetsMap {
  if (typeof window === 'undefined') return {}
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as unknown
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as SaverTargetsMap
    }
    return {}
  } catch {
    return {}
  }
}

function writeRaw(next: SaverTargetsMap): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    window.dispatchEvent(new CustomEvent(SAME_TAB_EVENT))
  } catch {
    // localStorage may be unavailable (private mode, quota); silent fallback.
  }
}

function subscribe(onChange: () => void): () => void {
  if (typeof window === 'undefined') return () => undefined
  const handleStorage = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY) onChange()
  }
  const handleSameTab = () => onChange()
  window.addEventListener('storage', handleStorage)
  window.addEventListener(SAME_TAB_EVENT, handleSameTab as EventListener)
  return () => {
    window.removeEventListener('storage', handleStorage)
    window.removeEventListener(SAME_TAB_EVENT, handleSameTab as EventListener)
  }
}

// Cached snapshot so React's "snapshot must be stable" invariant holds —
// otherwise useSyncExternalStore loops because every read returns a fresh object.
let _cachedRaw: string | null = null
let _cachedMap: SaverTargetsMap = {}

function getSnapshot(): SaverTargetsMap {
  if (typeof window === 'undefined') return _cachedMap
  const raw = window.localStorage.getItem(STORAGE_KEY)
  if (raw === _cachedRaw) return _cachedMap
  _cachedRaw = raw
  _cachedMap = readRaw()
  return _cachedMap
}

function getServerSnapshot(): SaverTargetsMap {
  return {}
}

export function useSaverTargets(): SaverTargetsMap {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
}

export function setSaverTarget(accountName: string, target: number | undefined): void {
  const current = readRaw()
  if (target == null || target <= 0 || !Number.isFinite(target)) {
    const { [accountName]: _removed, ...rest } = current
    void _removed
    writeRaw(rest)
    return
  }
  writeRaw({ ...current, [accountName]: { target } })
}
