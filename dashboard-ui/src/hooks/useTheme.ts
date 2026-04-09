import { useCallback, useEffect, useState } from 'react'

const STORAGE_KEY = 'atlas-theme'
type Theme = 'dark' | 'light'

function applyTheme(theme: Theme) {
  if (theme === 'light') {
    document.documentElement.classList.add('light')
  } else {
    document.documentElement.classList.remove('light')
  }
}

function readInitialTheme(): Theme {
  if (typeof window === 'undefined') return 'dark'
  const stored = window.localStorage.getItem(STORAGE_KEY)
  return stored === 'light' ? 'light' : 'dark'
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(readInitialTheme)

  useEffect(() => {
    applyTheme(theme)
    try {
      window.localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      /* ignore quota errors */
    }
  }, [theme])

  const toggleTheme = useCallback(() => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'))
  }, [])

  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if (e.key === 'l' || e.key === 'L') {
        const target = e.target as HTMLElement | null
        const tag = target?.tagName
        if (tag === 'INPUT' || tag === 'TEXTAREA' || target?.isContentEditable) return
        toggleTheme()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [toggleTheme])

  return { theme, toggleTheme }
}
