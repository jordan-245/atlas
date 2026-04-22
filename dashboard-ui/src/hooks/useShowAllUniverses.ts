import { useState } from 'react'

const STORAGE_KEY = 'atlas:showAllUniverses'

/**
 * Tiny boolean toggle backed by localStorage.
 * Default: false (show active universes only).
 */
export function useShowAllUniverses() {
  const [showAll, _setShowAll] = useState<boolean>(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === 'true'
    } catch {
      return false
    }
  })

  function setShowAll(value: boolean) {
    try {
      localStorage.setItem(STORAGE_KEY, String(value))
    } catch {
      // localStorage may be unavailable (private browsing, storage quota)
    }
    _setShowAll(value)
  }

  return { showAll, setShowAll }
}
