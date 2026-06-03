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

## Review
(fill after)
