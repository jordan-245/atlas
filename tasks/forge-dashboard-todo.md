# Forge Monitor Tab — build all 4 variants

## Backend
- [ ] services/api/forge.py — GET /api/forge/state (aggregate Hephaestus state)
- [ ] wire router into services/chat_server.py

## Frontend data layer
- [ ] src/api/forge-types.ts
- [ ] src/api/forge-queries.ts (useForgeState)
- [ ] src/api/keys.ts — add forge key
- [ ] index.css — forge keyframes (ember, pulse, travel, stamp)

## Components
- [ ] src/components/forge/ForgeTab.tsx — A/B/C/D switcher + shared data + countdown hook
- [ ] VariantForge.tsx (A — pipeline)
- [ ] VariantMissionControl.tsx (B — telemetry)
- [ ] VariantGauntlet.tsx (C — funnel)
- [ ] VariantNotebook.tsx (D — feed)

## Wiring
- [ ] App.tsx — replace research with forge
- [ ] TabBar.tsx — Forge tab
- [ ] lib/preloaders.ts — preloadForgeTab
- [ ] remove ResearchTab references

## Verify
- [x] npm run build passes (ForgeTab chunk 23.85 kB)
- [x] endpoint returns valid JSON (/api/forge/state, authed 200)
- [x] restart dashboard, confirm tab renders all 4 (Playwright shots, 0 console errors)

## Review
All 4 variants built behind a switcher in the new Forge tab (replaces Research). Backend
/api/forge/state aggregates live loop state. Committed surgically (29fe93ea) — pre-existing
Midas/finance staged batch left untouched. AWAITING operator pick → then delete unused 3 + switcher.
