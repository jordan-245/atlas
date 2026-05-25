/**
 * Atlas Context Injector Extension
 *
 * Events: session_start, before_agent_start
 *
 * On session_start:
 *   - Read system state (services, equity, config, alerts)
 *   - Display status widget with key metrics
 *
 * On before_agent_start:
 *   - Classify user prompt intent (research, trading, config, debugging, etc.)
 *   - Inject relevant context into system prompt so the agent starts oriented
 *
 * This eliminates the 2-5 minute orientation tax every session currently pays.
 *
 * Fixes applied (2026-05-25):
 *   A. Oneshot timer services (atlas-dashboard-refresh) no longer falsely flagged DOWN.
 *      A healthy oneshot = timer active + last result success, even when service is inactive.
 *   B. Portfolio equity derived from live broker state file (equity_history), not stale
 *      equity_curve log. File mtime drives freshness — stale (>24h) gets a warning label.
 *   C. Active markets discovered dynamically from config/active/*.json — decommissioned
 *      universes (asx, sector_etfs, commodity_etfs) are auto-excluded when their config
 *      file is absent.
 */

import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { existsSync, readFileSync, statSync, readdirSync } from "node:fs";
import { join } from "node:path";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function atlasRoot(): string {
  const envRoot = process.env.ATLAS_ROOT;
  if (envRoot) return envRoot;
  const cwd = process.cwd();
  if (existsSync(join(cwd, "config", "active"))) return cwd;
  return "/root/atlas";
}

function readJsonSafe<T>(path: string): T | null {
  try {
    if (!existsSync(path)) return null;
    return JSON.parse(readFileSync(path, "utf8")) as T;
  } catch {
    return null;
  }
}

function readTextSafe(path: string, maxLines = 50): string | null {
  try {
    if (!existsSync(path)) return null;
    const lines = readFileSync(path, "utf8").split("\n");
    return lines.slice(0, maxLines).join("\n");
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Fix C: Active market discovery from config/active/*.json
// Replaces the old hardcoded MARKETS = ["sp500", "asx"].
// Only markets with a live config file in config/active/ are included.
// Excludes: regime.json, *.bak*, *.pre*, archive/ subdir entries.
// ---------------------------------------------------------------------------

function discoverActiveMarkets(root: string): string[] {
  try {
    const activeDir = join(root, "config", "active");
    const files = readdirSync(activeDir)
      .filter(f => f.endsWith(".json"))
      .filter(f => f !== "regime.json")
      .filter(f => !f.includes(".bak") && !f.includes(".pre"));
    return files.map(f => f.replace(/\.json$/, ""));
  } catch {
    return ["sp500"]; // safe fallback
  }
}

// ---------------------------------------------------------------------------
// System state snapshot
// ---------------------------------------------------------------------------

interface ServiceStatus {
  name: string;
  active: boolean;
  status: string;
}

interface EquitySnapshot {
  date: string;
  equity: number;
  pnl: number;
  estimated?: boolean;
  staleDays?: number;   // > 0 if source file is stale (mtime > 24h)
  mtimeDate?: string;   // YYYY-MM-DD of source file mtime (for stale warning label)
}

interface ConfigSnapshot {
  market: string;
  version: string;
  mode: string;
  approvalRequired: boolean;
  enabledStrategies: string[];
  maxPositions: number;
}

interface SystemSnapshot {
  services: ServiceStatus[];
  failedServices: string[];      // core services that are not active AND have no healthy timer
  failedOptional: string[];      // optional services that crashed (status=failed)
  stoppedOptional: string[];     // optional services intentionally stopped (inactive)
  coreCount: number;
  coreUp: number;
  equity: Record<string, EquitySnapshot | null>;
  configs: ConfigSnapshot[];
  timestamp: string;
}

// Core services must be running continuously for daily trading operations.
// atlas-dashboard-refresh is a Type=oneshot service driven by an hourly timer —
// it is INTENTIONALLY inactive between firings. It lives in ONESHOT_TIMER_SERVICES.
const CORE_SERVICES = [
  "atlas-dashboard",
  "atlas-telegram-bot",
];

// Fix A: Oneshot services driven by systemd timers.
// These show "inactive" between firings, which is HEALTHY.
// Health check: corresponding .timer unit must be active + last Result=success.
const ONESHOT_TIMER_SERVICES = [
  "atlas-dashboard-refresh",
];

const OPTIONAL_SERVICES = [
  "atlas-director",
  "atlas-research-runner",
  "atlas-research-window",
];

const ATLAS_SERVICES = [...CORE_SERVICES, ...ONESHOT_TIMER_SERVICES, ...OPTIONAL_SERVICES];

// ---------------------------------------------------------------------------
// Fix A: Check if a oneshot service's timer is active and last run succeeded.
// Returns true  → service is healthy (scheduled, between firings).
// Returns false → timer is missing or failed → service is genuinely down.
// ---------------------------------------------------------------------------

async function isOneshotTimerHealthy(pi: ExtensionAPI, serviceName: string): Promise<boolean> {
  const timerName = serviceName.replace(/\.service$/, "") + ".timer";
  try {
    const result = await pi.exec("systemctl", [
      "show",
      timerName,
      "--property=ActiveState,Result",
    ], { timeout: 3000 });

    const props: Record<string, string> = {};
    for (const line of result.stdout.trim().split("\n")) {
      const eqIdx = line.indexOf("=");
      if (eqIdx >= 0) {
        props[line.slice(0, eqIdx).trim()] = line.slice(eqIdx + 1).trim();
      }
    }
    // Timer must be active AND last run must have succeeded
    return props["ActiveState"] === "active" && props["Result"] === "success";
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Fix B: Read equity from live broker state file (equity_history),
// with mtime-based freshness check.
//
// Priority:
//   1. brokers/state/live_{market}.json → equity_history[] (dual-write, daily update)
//   2. logs/equity_curve_{market}.json → fallback (legacy, may be stale)
//
// Freshness: statSync(filePath).mtime — if > 24h, staleDays > 0 and a stale label
// is shown. Never use a missing/undefined JSON date field; always fall back to mtime.
// ---------------------------------------------------------------------------

function readEquitySnapshot(root: string, market: string): EquitySnapshot | null {
  const MS_PER_DAY = 24 * 60 * 60 * 1000;

  // --- Primary: brokers/state/live_{market}.json equity_history ---
  const liveStatePath = join(root, "brokers", "state", `live_${market}.json`);
  const liveState = readJsonSafe<Record<string, unknown>>(liveStatePath);
  if (
    liveState &&
    Array.isArray(liveState.equity_history) &&
    liveState.equity_history.length > 0
  ) {
    const last = liveState.equity_history[liveState.equity_history.length - 1] as Record<string, unknown>;
    if (typeof last.equity === "number") {
      let staleDays = 0;
      let mtimeDate: string | undefined;
      try {
        const mtime = statSync(liveStatePath).mtime;
        const ageMs = Date.now() - mtime.getTime();
        staleDays = ageMs > MS_PER_DAY ? Math.floor(ageMs / MS_PER_DAY) : 0;
        mtimeDate = mtime.toISOString().slice(0, 10);
      } catch { /* ignore stat error */ }

      // Use the date field from the last equity_history entry if available;
      // fall back to mtime date. Never use eq.date if it doesn't exist.
      const date =
        (typeof last.date === "string" && last.date) ? last.date :
        (mtimeDate ?? new Date().toISOString().slice(0, 10));

      const pnl =
        typeof last.total_realized_pnl === "number" ? last.total_realized_pnl :
        typeof last.pnl === "number" ? last.pnl :
        0;

      return { date, equity: last.equity, pnl, staleDays, mtimeDate };
    }
  }

  // --- Fallback: logs/equity_curve_{market}.json ---
  const curvePath = join(root, "logs", `equity_curve_${market}.json`);
  const curve = readJsonSafe<Array<Record<string, unknown>>>(curvePath);
  if (curve && curve.length > 0) {
    const last = curve[curve.length - 1];
    if (typeof last.equity === "number") {
      let staleDays = 0;
      let mtimeDate: string | undefined;
      try {
        const mtime = statSync(curvePath).mtime;
        const ageMs = Date.now() - mtime.getTime();
        staleDays = ageMs > MS_PER_DAY ? Math.floor(ageMs / MS_PER_DAY) : 0;
        mtimeDate = mtime.toISOString().slice(0, 10);
      } catch { /* ignore */ }

      // For stale curve files, use mtime date instead of the embedded eq.date
      // (the embedded date is the last data point date, not the freshness date).
      const date = staleDays > 0
        ? (mtimeDate ?? new Date().toISOString().slice(0, 10))
        : (typeof last.date === "string" && last.date)
          ? last.date
          : (mtimeDate ?? new Date().toISOString().slice(0, 10));

      const pnl = typeof last.pnl === "number" ? last.pnl : 0;
      const estimated = last.estimated === true;

      return { date, equity: last.equity, pnl, estimated, staleDays, mtimeDate };
    }
  }

  return null;
}

async function getSystemSnapshot(pi: ExtensionAPI): Promise<SystemSnapshot> {
  const root = atlasRoot();

  // Fix C: discover active markets dynamically
  const markets = discoverActiveMarkets(root);

  // Check services — distinguish core from oneshot-timer from optional
  const services: ServiceStatus[] = [];
  const failedServices: string[] = [];
  const failedOptional: string[] = [];
  const stoppedOptional: string[] = [];
  const coreSet = new Set(CORE_SERVICES);
  const oneshotSet = new Set(ONESHOT_TIMER_SERVICES);
  let coreUp = 0;

  try {
    const result = await pi.exec("systemctl", [
      "is-active",
      ...ATLAS_SERVICES,
    ], { timeout: 5000 });
    const statuses = result.stdout.trim().split("\n");

    for (let i = 0; i < ATLAS_SERVICES.length; i++) {
      const name = ATLAS_SERVICES[i];
      const status = statuses[i]?.trim() ?? "unknown";
      const active = status === "active";

      if (coreSet.has(name)) {
        // Persistent core service
        services.push({ name, active, status });
        if (active) coreUp++;
        else failedServices.push(name);

      } else if (oneshotSet.has(name)) {
        // Fix A: Oneshot timer service — "inactive" is normal between firings.
        // Classify as healthy if the associated .timer is active + last Result=success.
        if (active) {
          // Rare: caught mid-run
          services.push({ name, active: true, status: "active" });
          coreUp++;
        } else {
          const timerHealthy = await isOneshotTimerHealthy(pi, name);
          if (timerHealthy) {
            // Healthy oneshot: timer scheduled, last run succeeded
            services.push({ name, active: true, status: "scheduled" });
            coreUp++;
          } else {
            // Timer missing or failed → genuinely down
            services.push({ name, active: false, status });
            failedServices.push(name);
          }
        }

      } else {
        // Optional service
        services.push({ name, active, status });
        if (active) { /* fine */ }
        else if (status === "failed") failedOptional.push(name);
        else stoppedOptional.push(name); // inactive = intentionally stopped
      }
    }
  } catch {
    for (const name of ATLAS_SERVICES) {
      services.push({ name, active: false, status: "unknown" });
      if (coreSet.has(name) || oneshotSet.has(name)) failedServices.push(name);
      else failedOptional.push(name);
    }
  }

  // Fix B: Read equity from live state file with mtime freshness
  const equity: Record<string, EquitySnapshot | null> = {};
  for (const market of markets) {
    equity[market] = readEquitySnapshot(root, market);
  }

  // Read configs (only for active markets)
  const configs: ConfigSnapshot[] = [];
  for (const market of markets) {
    const configPath = join(root, "config", "active", `${market}.json`);
    const config = readJsonSafe<Record<string, unknown>>(configPath);
    if (config) {
      const trading = (config.trading as Record<string, unknown>) ?? {};
      const risk = (config.risk as Record<string, unknown>) ?? {};
      const strategies = (config.strategies as Record<string, unknown>) ?? {};
      const enabled = Object.entries(strategies)
        .filter(([, v]) => (v as Record<string, unknown>)?.enabled === true)
        .map(([k]) => k);
      configs.push({
        market,
        version: String(config.version ?? "unknown"),
        mode: String(trading.mode ?? "unknown"),
        approvalRequired: trading.approval_required === true,
        enabledStrategies: enabled,
        maxPositions: Number(risk.max_open_positions ?? 0),
      });
    }
  }

  return {
    services,
    failedServices,
    failedOptional,
    stoppedOptional,
    coreCount: CORE_SERVICES.length + ONESHOT_TIMER_SERVICES.length,
    coreUp,
    equity,
    configs,
    timestamp: new Date().toISOString(),
  };
}

// ---------------------------------------------------------------------------
// Intent classification
// ---------------------------------------------------------------------------

type Intent =
  | "research"
  | "trading"
  | "config"
  | "debugging"
  | "strategy"
  | "deployment"
  | "data"
  | "health"
  | "general";

const INTENT_PATTERNS: Array<{ intent: Intent; patterns: RegExp[] }> = [
  {
    intent: "research",
    patterns: [
      /backtest/i, /sweep/i, /research/i, /experiment/i, /anneal/i,
      /optimize/i, /reoptimize/i, /sharpe/i, /brain/i, /hypothesis/i,
    ],
  },
  {
    intent: "trading",
    patterns: [
      /trade/i, /order/i, /position/i, /broker/i, /alpaca/i,
      /execute/i, /approve.*plan/i, /plan.*approve/i, /paper.*trad/i,
      /live.*trad/i, /entry|exit/i,
    ],
  },
  {
    intent: "config",
    patterns: [
      /config/i, /promot/i, /active_config/i, /parameter/i,
      /candidate/i, /version/i, /rollback/i, /backup/i,
    ],
  },
  {
    intent: "debugging",
    patterns: [
      /error/i, /fix/i, /bug/i, /crash/i, /fail/i, /broken/i,
      /incident/i, /diagnose/i, /debug/i, /traceback/i, /exception/i,
    ],
  },
  {
    intent: "strategy",
    patterns: [
      /strategy/i, /signal/i, /BaseStrategy/i, /generate_signals/i,
      /momentum/i, /mean.?reversion/i, /trend/i, /connors/i,
      /sector.?rotation/i, /opening.?gap/i,
    ],
  },
  {
    intent: "deployment",
    patterns: [
      /deploy/i, /restart/i, /service/i, /systemctl/i, /systemd/i,
      /daemon/i, /cron/i, /timer/i,
    ],
  },
  {
    intent: "data",
    patterns: [
      /data/i, /ingest/i, /cache/i, /ticker/i, /universe/i,
      /stale/i, /refresh/i, /download/i, /yfinance/i,
    ],
  },
  {
    intent: "health",
    patterns: [
      /health/i, /status/i, /check/i, /audit/i, /monitor/i,
      /dashboard/i, /alert/i,
    ],
  },
];

function classifyIntent(prompt: string): Intent {
  let bestIntent: Intent = "general";
  let bestScore = 0;

  for (const { intent, patterns } of INTENT_PATTERNS) {
    let score = 0;
    for (const pattern of patterns) {
      if (pattern.test(prompt)) score++;
    }
    if (score > bestScore) {
      bestScore = score;
      bestIntent = intent;
    }
  }

  return bestIntent;
}

// ---------------------------------------------------------------------------
// Equity display helper
// ---------------------------------------------------------------------------

function formatEquityLine(market: string, eq: EquitySnapshot): string {
  const pnlSign = eq.pnl >= 0 ? "+" : "";
  const base = `- ${market.toUpperCase()}: $${eq.equity.toFixed(2)} (realized PnL: ${pnlSign}$${eq.pnl.toFixed(2)}, as of ${eq.date})`;
  if ((eq.staleDays ?? 0) > 0) {
    return base + ` ⚠ stale (last file update: ${eq.mtimeDate ?? "unknown"})`;
  }
  if (eq.estimated) {
    return base + " [estimated]";
  }
  return base;
}

// ---------------------------------------------------------------------------
// Context injection
// ---------------------------------------------------------------------------

function buildInjection(intent: Intent, state: SystemSnapshot): string {
  const sections: string[] = [];

  // Always inject: system health summary
  const healthParts: string[] = [];
  if (state.failedServices.length === 0) {
    healthParts.push(`🟢 Core services: ${state.coreUp}/${state.coreCount} up`);
  } else {
    healthParts.push(`🔴 Core services down: ${state.failedServices.join(", ")}`);
  }
  if (state.failedOptional.length > 0) {
    healthParts.push(`⚠️ Crashed optional: ${state.failedOptional.join(", ")}`);
  }
  if (state.stoppedOptional.length > 0) {
    healthParts.push(`Research stopped (intentional): ${state.stoppedOptional.join(", ")}`);
  }
  sections.push(`## Atlas System State (auto-injected)\n${healthParts.join("\n")}`);

  // Always inject: equity & config summary
  const equityLines: string[] = [];
  for (const [market, eq] of Object.entries(state.equity)) {
    if (eq) {
      equityLines.push(formatEquityLine(market, eq));
    }
  }
  if (equityLines.length > 0) {
    sections.push(`### Portfolio\n${equityLines.join("\n")}`);
  }

  for (const cfg of state.configs) {
    sections.push(
      `### Config: ${cfg.market.toUpperCase()} ${cfg.version}\n` +
      `- Mode: ${cfg.mode} | Approval: ${cfg.approvalRequired} | Max positions: ${cfg.maxPositions}\n` +
      `- Strategies (${cfg.enabledStrategies.length}): ${cfg.enabledStrategies.join(", ")}`
    );
  }

  // Intent-specific context
  switch (intent) {
    case "research":
      sections.push(
        "### Research Context\n" +
        "- Backtests: `python scripts/cli.py backtest -m <market>` or use `atlas_jobs_run` tool\n" +
        "- Brain knowledge base: `brain/` directory (check INDEX.md for prior results)\n" +
        "- Research queue: `research/queue/` for pending experiments\n" +
        "- Key metrics to track: Sharpe, CAGR, max drawdown, profit factor, trade count\n" +
        "- LESSON: Always check brain/ before running a backtest to avoid re-testing"
      );
      break;

    case "trading":
      sections.push(
        "### Trading Context\n" +
        "- Broker: Alpaca (ACTIVE account, commission-free)\n" +
        "- Paper engine: `paper_engine/` directory\n" +
        "- Plan flow: ingest → plan → approve → execute\n" +
        "- CLI: `scripts/cli.py [ingest|plan|approve|paper-run|status|ledger]`\n" +
        "- LESSON: Never write paper_state files when broker is offline\n" +
        "- LESSON: Always verify plan status is APPROVED before execution"
      );
      break;

    case "config":
      sections.push(
        "### Config Context\n" +
        "- Active configs: `config/active/sp500.json` (and other active markets)\n" +
        "- Candidates: `config/candidates/`\n" +
        "- Backups: `config/versions/active_config_pre_reopt_*.json`\n" +
        "- Promotion flow: validate OOS → risk gate check → backup → copy → verify\n" +
        "- Use `atlas_risk_check_config_promotion` tool before any promotion\n" +
        "- LESSON: Always bump config version when promoting"
      );
      break;

    case "debugging":
      sections.push(
        "### Debugging Context\n" +
        "- Logs directory: `logs/` (healthz, intraday, equity curves)\n" +
        "- Service logs: `journalctl -u atlas-<service> --no-pager -n 50`\n" +
        "- Common issues:\n" +
        "  - OOM kills on research-runner (check memory limits)\n" +
        "  - Stale data cache causing bad signals (refresh with ingest)\n" +
        "  - Strategy API drift (check generate_signals signature)\n" +
        "- LESSON: Check journalctl first, then code, then config"
      );
      break;

    case "strategy":
      sections.push(
        "### Strategy Context\n" +
        "- Base class: `strategies/base_strategy.py` (BaseStrategy)\n" +
        "- Required methods: `generate_signals(data, config) -> DataFrame`\n" +
        "- Strategy dir: `strategies/` (one file per strategy)\n" +
        "- Test: `python -c \"from strategies.X import X; X()\"` for import check\n" +
        "- LESSON: Dormant strategies drift — always test import after editing\n" +
        "- LESSON: Strategy changes require service restart if research-runner is active"
      );
      break;

    case "deployment":
      sections.push(
        "### Deployment Context\n" +
        "- Services: " + ATLAS_SERVICES.join(", ") + "\n" +
        "- Service files: `/etc/systemd/system/atlas-*.service`\n" +
        "- Restart: `systemctl restart atlas-<name>`\n" +
        "- File → Service mapping:\n" +
        "  - `strategies/*` → atlas-research-runner\n" +
        "  - `dashboard/*` → atlas-dashboard, atlas-dashboard-refresh\n" +
        "  - `scripts/director_cron.py` → atlas-director\n" +
        "  - `scripts/telegram_bot.py` → atlas-telegram-bot\n" +
        "- LESSON: Always restart affected services after code changes"
      );
      break;

    case "data":
      sections.push(
        "### Data Context\n" +
        "- Cache: `data/cache/` (yfinance price data)\n" +
        "- Ingest: `python scripts/cli.py ingest -m <market>`\n" +
        "- Universe: `data/universe_sp500.json`\n" +
        "- Check freshness: look at cache file mtimes\n" +
        "- LESSON: Always ingest fresh data before backtesting or plan generation"
      );
      break;

    case "health":
      sections.push(
        "### Health Check Context\n" +
        "- Quick check: `python scripts/health_check.py`\n" +
        "- Full audit: use atlas-healthz skill\n" +
        "- Dashboard: https://localhost:8501 (auth-protected)\n" +
        "- Telegram alerts: check bot for recent messages\n" +
        "- Services: " + state.services.map(s => `${s.active ? "🟢" : "🔴"} ${s.name}${s.status === "scheduled" ? " (scheduled)" : ""}`).join(", ")
      );
      break;
  }

  return sections.join("\n\n");
}

// ---------------------------------------------------------------------------
// Widget formatting
// ---------------------------------------------------------------------------

function formatStatusWidget(state: SystemSnapshot): string[] {
  const lines: string[] = [];

  // Line 1: Equity summary
  const eqParts: string[] = [];
  for (const [market, eq] of Object.entries(state.equity)) {
    if (eq) {
      const pnlSign = eq.pnl >= 0 ? "+" : "";
      let entry = `${market.toUpperCase()} $${eq.equity.toFixed(2)} (${pnlSign}$${eq.pnl.toFixed(2)})`;
      if ((eq.staleDays ?? 0) > 0) {
        entry += ` ⚠${eq.staleDays}d stale`;
      }
      eqParts.push(entry);
    }
  }
  if (eqParts.length > 0) {
    lines.push(`💰 ${eqParts.join("  |  ")}`);
  }

  // Line 2: Config versions + strategies (compact)
  const cfgParts: string[] = [];
  for (const cfg of state.configs) {
    cfgParts.push(`${cfg.market.toUpperCase()} ${cfg.version} · ${cfg.enabledStrategies.length} strats · ${cfg.mode}`);
  }
  if (cfgParts.length > 0) {
    lines.push(`📊 ${cfgParts.join("  |  ")}`);
  }

  // Line 3: Service health — core vs optional
  const coreParts: string[] = [];
  if (state.failedServices.length === 0) {
    coreParts.push(`🟢 ${state.coreUp}/${state.coreCount} core up`);
  } else {
    coreParts.push(`🔴 Core down: ${state.failedServices.map(s => s.replace("atlas-", "")).join(", ")}`);
  }

  // Optional services: only mention if crashed (failed), skip if just stopped
  if (state.failedOptional.length > 0) {
    coreParts.push(`⚠️ crashed: ${state.failedOptional.map(s => s.replace("atlas-", "")).join(", ")}`);
  }
  if (state.stoppedOptional.length > 0) {
    coreParts.push(`${state.stoppedOptional.length} research stopped`);
  }

  lines.push(coreParts.join("  |  "));

  return lines;
}

// ---------------------------------------------------------------------------
// Extension entry point
// ---------------------------------------------------------------------------

// Cache the snapshot so we don't re-read on every turn
let cachedSnapshot: SystemSnapshot | null = null;
let snapshotAge = 0;
const SNAPSHOT_TTL_MS = 5 * 60 * 1000; // 5 minutes

export default function atlasContextInjector(pi: ExtensionAPI) {

  async function getOrRefreshSnapshot(): Promise<SystemSnapshot> {
    const now = Date.now();
    if (cachedSnapshot && (now - snapshotAge) < SNAPSHOT_TTL_MS) {
      return cachedSnapshot;
    }
    cachedSnapshot = await getSystemSnapshot(pi);
    snapshotAge = now;
    return cachedSnapshot;
  }

  // --- session_start: Cache initial snapshot (widget disabled) ---
  pi.on("session_start", async (_event, _ctx) => {
    try {
      await getOrRefreshSnapshot();
    } catch {
      // Silently fail — snapshot will be retried on first agent turn
    }
  });

  // --- before_agent_start: Inject context into system prompt ---
  pi.on("before_agent_start", async (event, ctx) => {
    try {
      const prompt = event.prompt ?? "";
      if (!prompt.trim()) return;

      const intent = classifyIntent(prompt);
      const state = await getOrRefreshSnapshot();
      const injection = buildInjection(intent, state);

      return {
        systemPrompt: event.systemPrompt + "\n\n" + injection,
      };
    } catch {
      // Silently fail — don't block the agent
      return;
    }
  });
}
