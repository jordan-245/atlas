# Make the Finance tab LIVE via Up Bank webhook

User: finance tab data is stale/incorrect; "i want the data on there to be live"; "the webhook works".

## State found
- Up Bank data synced ONCE daily at 06:30 (/etc/cron.d/up-bank --full-resync). Stale intraday.
- Old standalone webhook server (up_webhook_server.py) RETIRED: port :8000 collided with
  supercoach API (still does), and it silently broke once. NO webhook registered with Up now.
- cloudflared tunnel routes ONE hostname: atlas.getflowtide.com -> 127.0.0.1:8899 (dashboard).
- up_sync.py supports fast INCREMENTAL sync (no flag). FX rate hardcoded 0.63.
- Dashboard auth is per-route (Depends(check_auth)); global middleware only adds headers.

## Design (live, robust)
Mount a PUBLIC, HMAC-verified `POST /api/up/webhook` on the existing :8899 FastAPI app
(reachable at https://atlas.getflowtide.com/api/up/webhook via the existing tunnel — no new
port, no collision). On a transaction event it triggers a coalesced incremental up_sync
(refreshes balances + transactions) and invalidates the /api/finance cache => dashboard live.
Keep a periodic incremental sync as a SAFETY NET (old webhook died silently — don't repeat).

## Tasks
- [ ] services/api/up_webhook.py: POST /api/up/webhook (HMAC verify, event dispatch,
      coalesced background incremental sync), GET /api/up/webhook/health (auth)
- [ ] finance.py: add invalidate_cache(); call on webhook
- [ ] mount router in services/chat_server.py; restart; verify 401-on-bad-sig
- [ ] /root/up-bank/manage_webhook.py: list / register <url> / ping <id> / delete <id>;
      save returned secretKey -> ~/.atlas-secrets.json up_webhook_secret
- [ ] register webhook -> ping -> confirm receipt + sync fired + cache invalidated
- [ ] safety net: incremental sync every ~10 min in /etc/cron.d/up-bank (keep daily full)
- [ ] data-correctness fixes folded in (from the audit):
      - last_updated -> real sync time (sync_state) so freshness is truthful
      - live FX rate (cached, fallback to constant) replacing hardcoded 0.63
      - pace_status vs BUDGET line (not income) — matches historical pace + pace_diff
      - month length via calendar.monthrange (not hardcoded 30)
- [ ] end-to-end verify: make a tiny real txn OR ping; dashboard reflects within seconds

## Review — DONE & VERIFIED

Finance tab is now LIVE. Root cause of "incorrect data": the dashboard only synced Up Bank
once daily at 06:30, so it was missing ALL of today's transactions (incl a $620 charge);
monthly spend showed 323.85 when it was actually 987.30. Plus the FX rate was hardcoded at
0.63 while live is 0.7175 (~14% stale) — overstating net worth by ~$258.

Shipped:
- POST /api/up/webhook on :8899 (HMAC-verified, public, via the existing tunnel). Registered
  with Up Bank (id 40f046a5…); Up PING DELIVERED 200; signed TRANSACTION_CREATED fires a
  coalesced incremental up_sync (~5-9s) -> balances+txns refreshed -> /api/finance cache
  invalidated. /api/up/webhook/health exposes last event/sync for monitoring.
- Safety-net incremental sync every 15 min in /etc/cron.d/up-bank (kept daily 06:30 full).
- Live USD/AUD FX cached in sync_state, read by build_finance_payload (fallback to constant).
- pace_status now vs the budget line (was vs income -> always "over" pre-payday).
- month length via calendar.monthrange (was hardcoded 30).

Verified: /api/finance shows up_bank 14,982.49 (today's txns in), FX 0.7175, atlas_aud
1862.08 (was 2120.70), total 16,844.57. Atlas commit 2f4eff9c. up-bank has no git repo —
changes saved on disk (live).

Residual (noted, not blocking): early-month projected_total extrapolates a 2-3 day sample
(one $620 one-off inflates it) — design characteristic, not a bug. Could add a low-confidence
indicator for the first few days.
