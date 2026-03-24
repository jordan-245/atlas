/**
 * Persistent swarm state — written to .pi-swarm/state.json during runs.
 *
 * This is the bridge between the swarm extension (running inside pi)
 * and the standalone dashboard (running in another terminal).
 * Updated on every agent status change via atomic write.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import type { AgentDiagnostics, MergeResult, SwarmAgent, SwarmPlan, UsageStats } from "./types.js";

export interface SwarmRunState {
  /** Run ID (timestamp-based) */
  runId: string;
  /** What the swarm is doing */
  objective: string;
  /** Overall run status */
  status: "planning" | "scouting" | "building" | "reviewing" | "merging" | "completed" | "failed";
  /** Planned complexity */
  complexity: "simple" | "moderate" | "complex";
  /** Git branch we're merging into */
  baseBranch: string;
  /** Repo root */
  repoRoot: string;
  /** When the run started */
  startedAt: string;
  /** When it finished (null if still running) */
  completedAt: string | null;
  /** All agents in this run */
  agents: SwarmAgentState[];
  /** Merge results (populated after merge phase) */
  mergeResults: MergeResult[];
  /** Aggregate usage */
  totalUsage: UsageStats;
  /** Plan reasoning */
  planReasoning: string;
  /** Last time this file was updated */
  updatedAt: string;
  /** Phase timeline with timing (optional — populated during runs) */
  phases?: { name: string; capability: string; startedAt: string; completedAt: string | null; agents: string[] }[];
  /** Global activity feed (last 50 entries) */
  activityFeed?: { timestamp: string; agent: string; type: string; summary: string }[];
  /** Token rate samples (~every 5s) for sparkline data */
  tokenRates?: { timestamp: string; inputTotal: number; outputTotal: number; costTotal: number }[];
  /** Aggregate file change summary across all builders */
  filesChanged?: { path: string; agent: string; linesAdded: number; linesRemoved: number }[];
}

export interface SwarmAgentState {
  name: string;
  capability: string;
  task: string;
  status: "pending" | "running" | "completed" | "failed" | "merged" | "merge_failed";
  exitCode: number | null;
  startedAt: string | null;
  completedAt: string | null;
  usage: UsageStats;
  model?: string;
  files?: string[];
  worktree: { path: string; branch: string } | null;
  /** Rolling log of recent activity (last few lines of output) */
  lastOutput: string;
  /** Rich tracking fields (optional — populated during live runs) */
  toolCalls?: { name: string; count: number; lastAt: string }[];
  filesModified?: { path: string; linesAdded: number; linesRemoved: number }[];
  activityLog?: { timestamp: string; type: string; summary: string }[];
  tokensPerSecond?: number;
  currentTurn?: number;
  progressPct?: number | null;
  /** Diagnostic info populated when status = "failed" */
  diagnostics?: AgentDiagnostics;
}

const STATE_DIR = ".pi-swarm";
const STATE_FILE = "state.json";
const HISTORY_FILE = "history.jsonl";

/**
 * Get the state file path for a repo.
 */
export function getStatePath(repoRoot: string): string {
  return path.join(repoRoot, STATE_DIR, STATE_FILE);
}

/**
 * Get the history file path for a repo.
 */
export function getHistoryPath(repoRoot: string): string {
  return path.join(repoRoot, STATE_DIR, HISTORY_FILE);
}

/**
 * Write the current run state to disk.
 * Uses atomic write (write to tmp, rename) to prevent partial reads.
 */
export function writeState(repoRoot: string, state: SwarmRunState): void {
  const dir = path.join(repoRoot, STATE_DIR);
  fs.mkdirSync(dir, { recursive: true });

  state.updatedAt = new Date().toISOString();

  const statePath = getStatePath(repoRoot);
  const tmpPath = statePath + ".tmp";
  fs.writeFileSync(tmpPath, JSON.stringify(state, null, 2) + "\n");
  fs.renameSync(tmpPath, statePath);
}

/**
 * Read the current run state from disk.
 * Returns null if no state file exists.
 */
export function readState(repoRoot: string): SwarmRunState | null {
  const statePath = getStatePath(repoRoot);
  if (!fs.existsSync(statePath)) return null;

  try {
    const text = fs.readFileSync(statePath, "utf-8");
    return JSON.parse(text) as SwarmRunState;
  } catch {
    return null;
  }
}

/**
 * Append a completed run summary to the history file (JSONL).
 */
export function appendHistory(repoRoot: string, state: SwarmRunState): void {
  const historyPath = getHistoryPath(repoRoot);
  const dir = path.join(repoRoot, STATE_DIR);
  fs.mkdirSync(dir, { recursive: true });

  const summary = {
    runId: state.runId,
    objective: state.objective,
    complexity: state.complexity,
    status: state.status,
    agentCount: state.agents.length,
    startedAt: state.startedAt,
    completedAt: state.completedAt,
    totalUsage: state.totalUsage,
    mergedCount: state.mergeResults.filter((r) => r.success).length,
  };

  fs.appendFileSync(historyPath, JSON.stringify(summary) + "\n");
}

/**
 * Convert a SwarmAgent (runtime) to a SwarmAgentState (serializable).
 */
export function agentToState(agent: SwarmAgent, lastOutput?: string): SwarmAgentState {
  return {
    name: agent.name,
    capability: agent.capability,
    task: agent.task,
    status: agent.status,
    exitCode: agent.exitCode,
    startedAt: agent.startedAt,
    completedAt: agent.completedAt,
    usage: { ...agent.usage },
    model: agent.model,
    files: agent.files,
    worktree: agent.worktree ? { path: agent.worktree.path, branch: agent.worktree.branch } : null,
    lastOutput: lastOutput ?? "",
    // Rich tracking fields
    toolCalls: agent.toolCalls,
    filesModified: agent.filesModified,
    activityLog: agent.activityLog,
    tokensPerSecond: agent.tokensPerSecond,
    currentTurn: agent.currentTurn,
    progressPct: agent.progressPct,
    // Diagnostic info (populated on failure)
    diagnostics: agent.diagnostics,
  };
}

// ── Rich state helper functions ──────────────────────────────────────

/**
 * Append an entry to the global activity feed (capped at 50 entries).
 * Mutates state in place — call writeState() after to persist.
 */
export function appendActivity(
  state: SwarmRunState,
  entry: { timestamp: string; agent: string; type: string; summary: string },
): void {
  state.activityFeed = state.activityFeed ?? [];
  state.activityFeed.push(entry);
  if (state.activityFeed.length > 50) {
    state.activityFeed = state.activityFeed.slice(-50);
  }
}

/**
 * Append an entry to a specific agent's activity log (capped at 20 entries).
 * Mutates state in place — call writeState() after to persist.
 */
export function updateAgentActivity(
  state: SwarmRunState,
  agentName: string,
  entry: { timestamp: string; type: string; summary: string },
): void {
  const agent = state.agents.find((a) => a.name === agentName);
  if (!agent) return;
  agent.activityLog = agent.activityLog ?? [];
  agent.activityLog.push(entry);
  if (agent.activityLog.length > 20) {
    agent.activityLog = agent.activityLog.slice(-20);
  }
}

/**
 * Sample current cumulative token totals for sparkline data (capped at 120 samples).
 * Mutates state in place — call writeState() after to persist.
 */
export function recordTokenRate(state: SwarmRunState): void {
  const u = state.totalUsage;
  state.tokenRates = state.tokenRates ?? [];
  state.tokenRates.push({
    timestamp: new Date().toISOString(),
    inputTotal: u.input,
    outputTotal: u.output,
    costTotal: u.cost,
  });
  if (state.tokenRates.length > 120) {
    state.tokenRates = state.tokenRates.slice(-120);
  }
}
