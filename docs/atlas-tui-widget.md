# Atlas TUI Widget — Runbook

## Overview

The `atlas-tui-widget` Pi extension renders a compact live-activity dashboard above the Pi editor. It uses the Dashboard Pro visual style: dense, data-first, monochrome with status colors.

```
◆ ATLAS  │  tools 17  ✓ 15  │  ✗ 2  │  ⏱ 02:34  │  ⟳ 3 delegated
──────────────────────────────────────────────────────────────────────
  ✓ Read              memory/SUMMARY.md                          200ms
  ✓ Bash              git status --short                          89ms
  ⟳ atlas_jobs_run    health_check                               1.2s…
  ✗ Write             tasks/todo.md                               12ms
```

The footer status bar also shows a compact one-liner:

```
◆ atlas │ ⟳ 1 running │ ✓14 / ✗1 │ ⏱ 02:34
```

---

## Commands

| Command             | Effect                                            |
|---------------------|---------------------------------------------------|
| `/atlas-tui`        | Toggle widget on/off                              |
| `/atlas-tui reset`  | Clear all session stats and restart elapsed timer |
| `/reload`           | Hot-reload the extension (picks up code changes)  |

---

## Enable / Disable

The widget auto-mounts on `session_start`. If you want it permanently disabled, remove the extension from the pi-package registration (see **Package Wiring** below).

To hide for the current session only, run `/atlas-tui`.

---

## Package Wiring

The extension is registered in `pi-package/atlas-ops/package.json`:

```json
"pi": {
  "extensions": [
    ...
    "./extensions/atlas-tui-widget/src"
  ]
}
```

This means it loads automatically whenever the atlas-ops pi-package is active (`pi` from `/root/atlas`).

---

## Architecture

```
atlas-tui-widget/
  src/
    index.ts     Extension entry point (Pi runtime wiring)
    core.ts      Pure logic + state management (testable without Pi)
  tests/
    verify.ts    Standalone verification script (no Pi required)
```

**Pure functions** (exported for testing):

| Function | Purpose |
|---|---|
| `createState()` | Fresh `TuiState` with session timer started |
| `renderWidget(state, theme, width, widthFns)` | Returns `string[]`, each line ≤ `width`; `widthFns` is injected so tests can mock ANSI-aware truncation |
| `renderStatus(state, theme)` | One-line footer string |
| `summarizeArgs(args)` | Extract relevant arg from tool call params |
| `fmtDuration(ms)` | Human-readable duration |
| `statusColor(status)` | Map status → theme color name |
| `statusIcon(status)` | Map status → `✓`/`✗`/`⟳` |

**Events subscribed:**

| Event | Action |
|---|---|
| `session_start` | Reset state, mount widget |
| `session_shutdown` | Unmount widget + status |
| `agent_start` / `agent_end` | Refresh footer status |
| `tool_execution_start` | Add running entry, increment totals |
| `tool_execution_end` | Move to completed, record duration |

**Delegation detection:** `subagent`, `swarm`, and any tool starting with `delegate` increment the delegation counter.

**Memory bounds:** `recentActivity` is capped at `MAX_ACTIVITY = 5` entries (oldest evicted via `shift()`). `activeTools` is defensively capped by `capActiveTools()` (default `4 × MAX_ACTIVITY = 20` entries, evicting oldest in Map insertion order) to guard against missed `tool_execution_end` events in aborted sessions. The rendered feed is always capped at `MAX_ACTIVITY = 5` rows regardless of how many tools are concurrently in-flight.

---

## Testing

Run the verification script (no Pi session required):

```bash
cd /root/atlas/pi-package/atlas-ops
npm run verify-tui

# Or directly:
npx tsx extensions/atlas-tui-widget/tests/verify.ts
```

Tests cover:
1. **Width-safety** — every rendered line ≤ requested width, for widths 40–120
2. **Status/color** — `statusColor()` and `statusIcon()` mappings; widget content includes correct icons
3. **Bounded memory** — `recentActivity` never exceeds `MAX_ACTIVITY`; rendered feed capped at `MAX_ACTIVITY` rows even when more tools are concurrently in-flight
4. **FIFO eviction** — oldest entries dropped first
5. **summarizeArgs** — path priority, newline stripping, fallback
6. **fmtDuration** — ms/s/m:ss formatting
7. **`capActiveTools`** — evicts oldest `activeTools` entries when the map exceeds the limit; no-op when under the limit; safe on empty map

---

## TypeScript Check

```bash
cd /root/atlas/pi-package/atlas-ops
npx tsc --noEmit
```

Extensions load via jiti (no compilation step), but tsc catches type errors early.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Widget not appearing | Check `ctx.hasUI` — widget is suppressed in `-p`/JSON mode |
| Widget disappeared | Run `/atlas-tui` to re-enable |
| Type errors on `@mariozechner/pi-tui` | Pi resolves this via its own node_modules at load time |
| All tests fail | Ensure Node ≥ 18 and `npx tsx` is available |

---

## Future Improvements

- Token / cost metrics from `ctx.sessionManager.getBranch()` (requires iterating message usage)
- Collapsible widget (keyboard shortcut to hide without `/atlas-tui`)
- Atlas equity / service health inline (re-use `atlas-status-dashboard` data)
