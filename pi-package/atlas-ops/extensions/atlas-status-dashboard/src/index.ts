/**
 * Atlas Status Dashboard Extension
 *
 * Events: session_start, turn_end
 *
 * Displays real-time Atlas system status in the Pi footer status bar.
 * Updates after every turn so you always see the current state.
 *
 * Shows: equity, service health, config version, active alerts
 */

import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
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

// ---------------------------------------------------------------------------
// Quick state readers (optimized for speed — called every turn)
// ---------------------------------------------------------------------------

interface QuickState {
  healthEmoji: string;
  healthSummary: string;
  equitySummary: string;
  configVersion: string;
  alertCount: number;
  researchStatus: string;
}

async function getQuickState(pi: ExtensionAPI): Promise<QuickState> {
  const root = atlasRoot();

  // Services — quick check
  let healthEmoji = "🟢";
  let healthSummary = "healthy";
  let failedCount = 0;
  try {
    const result = await pi.exec("systemctl", [
      "is-active",
      "atlas-dashboard",
      "atlas-dashboard-refresh",
      "atlas-telegram-bot",
      "atlas-director",
      "atlas-research-runner",
      "atlas-research-window",
    ], { timeout: 3000 });
    const statuses = result.stdout.trim().split("\n");
    failedCount = statuses.filter(s => s.trim() !== "active").length;
    if (failedCount > 0) {
      healthEmoji = failedCount >= 3 ? "🔴" : "🟡";
      healthSummary = `${failedCount} down`;
    }
  } catch {
    healthEmoji = "⚪";
    healthSummary = "unknown";
  }

  // Equity — last entry from SP500 curve (primary market)
  let equitySummary = "–";
  const sp500Curve = readJsonSafe<Array<{ equity: number; pnl: number }>>(
    join(root, "logs", "equity_curve_sp500.json")
  );
  if (sp500Curve && sp500Curve.length > 0) {
    const latest = sp500Curve[sp500Curve.length - 1];
    const pnlSign = latest.pnl >= 0 ? "+" : "";
    equitySummary = `$${latest.equity.toFixed(0)} (${pnlSign}$${latest.pnl.toFixed(2)})`;
  }

  // Config version
  let configVersion = "?";
  const sp500Config = readJsonSafe<{ version: string }>(
    join(root, "config", "active", "sp500.json")
  );
  if (sp500Config?.version) {
    configVersion = sp500Config.version;
  }

  // Active alerts — count recent error log files (last 24h)
  let alertCount = 0;
  try {
    const logsDir = join(root, "logs");
    if (existsSync(logsDir)) {
      const now = Date.now();
      const cutoff = now - 24 * 60 * 60 * 1000;
      const files = readdirSync(logsDir);
      for (const file of files) {
        if (!file.endsWith(".log")) continue;
        try {
          const stat = statSync(join(logsDir, file));
          if (stat.mtimeMs > cutoff && stat.size > 0) {
            // Quick check if log has errors
            const tail = readFileSync(join(logsDir, file), "utf8").slice(-2000);
            if (/error|exception|traceback|failed|critical/i.test(tail)) {
              alertCount++;
            }
          }
        } catch {
          // skip unreadable files
        }
      }
    }
  } catch {
    // skip alert counting on error
  }

  // Research status — check if research-runner is active
  let researchStatus = "idle";
  try {
    const rrStatus = await pi.exec("systemctl", ["is-active", "atlas-research-runner"], { timeout: 2000 });
    if (rrStatus.stdout.trim() === "active") {
      researchStatus = "running";
    } else {
      researchStatus = "stopped";
    }
  } catch {
    researchStatus = "unknown";
  }

  return {
    healthEmoji,
    healthSummary,
    equitySummary,
    configVersion,
    alertCount,
    researchStatus,
  };
}

// ---------------------------------------------------------------------------
// Status formatting
// ---------------------------------------------------------------------------

function formatStatusLine(state: QuickState): string {
  const parts: string[] = [];

  parts.push(`${state.healthEmoji} ${state.healthSummary}`);
  parts.push(`💰 ${state.equitySummary}`);
  parts.push(`📋 ${state.configVersion}`);

  if (state.researchStatus === "running") {
    parts.push("🔬 research");
  }

  if (state.alertCount > 0) {
    parts.push(`⚠️ ${state.alertCount} alert${state.alertCount > 1 ? "s" : ""}`);
  }

  return parts.join(" │ ");
}

// ---------------------------------------------------------------------------
// Extension entry point
// ---------------------------------------------------------------------------

// NOTE: Display disabled — equity/P&L status panel removed per user request.
// The atlas-tui-widget (aboveEditor) now owns all live status display.
// Internal helpers (getQuickState, formatStatusLine) are preserved for
// programmatic use if needed in future.

export default function atlasStatusDashboard(_pi: ExtensionAPI) {
  // No-op: status bar registration removed so equity/P&L no longer
  // appears below the editor input area.
}
