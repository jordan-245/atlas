# IB Gateway (paper) — headless stack

Pre-built infrastructure for IB_MICRO_ADAPTER_PLAN Phase A/B. Everything here is
ready; the ONLY missing piece is IBKR paper credentials.

## Bring-up (once credentials exist)

```bash
cd /root/atlas/ops/ib-gateway
cp .env.example .env && $EDITOR .env          # paper username/password
ln -sf $PWD/atlas-ib-gateway.service /etc/systemd/system/
ln -sf $PWD/atlas-ib-watchdog.service /etc/systemd/system/
ln -sf $PWD/atlas-ib-watchdog.timer  /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now atlas-ib-gateway atlas-ib-watchdog.timer
# wait ~2-3 min for login, then:
docker ps                                      # health: healthy
python3 smoke_e2e.py                           # full adapter-level E2E (Phase B.5)
```

## Pieces

| file | role |
|---|---|
| `docker-compose.yml` | gnzsnz/ib-gateway **pinned 10.37.1r**, paper mode, 05:00 ET auto-restart, loopback-only ports (4002 API, 5900 VNC), TCP healthcheck |
| `.env.example` | credential template (`.env` is gitignored) |
| `atlas-ib-gateway.service` | systemd wrapper (compose up/down) |
| `watchdog.py` + `.service`/`.timer` | 10-min health check → Telegram after 2 consecutive failures (daily restart never pages); recovery message on green |
| `smoke_e2e.py` | adapter-level E2E: connect → account → MES price → far-limit order → cancel → positions |

## Design notes

- **Paper creds bypass IB-Key 2FA** — the whole reason unattended works. `TWOFA_TIMEOUT_ACTION=exit`: if a 2FA prompt ever appears, creds are wrong (live?) — die loudly.
- **Version pinned** (10.37.1r): IBC auto-restart regressions are version-pair specific. Bump deliberately; never `latest`.
- **Keep the paper account funded** (simulated $) — unfunded paper accounts break IBC's restart token (IbcAlpha/IBC#345).
- 05:00 ET restart sits after Globex overnight maintenance and hours before any OPG/open work.
- Atlas adapter default port is 7497 (TWS paper); this stack exposes **4002** — broker config needs `ib: {port: 4002}` or pass `--port` to the smoke.
- Debug UI: `ssh -L 5900:localhost:5900 <vps>` then VNC to localhost:5900.
