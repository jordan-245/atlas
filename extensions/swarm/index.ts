/**
 * pi-swarm: Parallel code modification via agent swarm.
 *
 * Registers tools:
 *   - swarm         — Run a full swarm: plan → scout → build → review → merge
 *   - swarm_plan    — Analyze complexity and propose agent allocation
 *   - swarm_merge   — Merge completed agent branches
 *   - swarm_status  — Show active swarm state
 *   - swarm_cleanup — Remove all swarm worktrees and branches
 *
 * Architecture:
 *   - Builders get isolated git worktrees (parallel writes, no conflicts)
 *   - Scouts/reviewers run against main working tree (read-only)
 *   - Guard extensions enforce capability constraints per agent
 *   - After builders complete, branches merge sequentially (FIFO)
 *   - Complexity analysis determines agent count within budget
 */

import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type { AgentToolResult } from "@mariozechner/pi-agent-core";
import type { Message } from "@mariozechner/pi-ai";
import { StringEnum } from "@mariozechner/pi-ai";
import { type ExtensionAPI, getMarkdownTheme, getAgentDir, parseFrontmatter } from "@mariozechner/pi-coding-agent";
import { Container, Markdown, Spacer, Text } from "@mariozechner/pi-tui";
import { Type } from "@sinclair/typebox";

import { deployGuard, removeGuard } from "./guards.js";
import { initMailStore, getMailPath, deployMailExtension, listMail, formatForInjection } from "./mail.js";
import { mergeAll, checkMergeability, runPostMergeTests } from "./merge.js";
import { planSwarm } from "./planner.js";
import { writeState, appendHistory, agentToState, appendActivity, recordTokenRate, type SwarmRunState } from "./state.js";
import type {
  AgentCapability,
  MergeResult,
  PlannedAgent,
  SwarmAgent,
  SwarmConfig,
  SwarmPlan,
  UsageStats,
  WorktreeInfo,
} from "./types.js";
import { DEFAULT_SWARM_CONFIG, emptyUsage } from "./types.js";
import { createWorktree, cleanupWorktrees, getCurrentBranch, getRepoRoot, listWorktrees, createReviewWorktree, removeReviewWorktree } from "./worktree.js";

// ── Widget integration ───────────────────────────────────────────────
import { renderAgentWidget, agentToWidgetState, formatWidgetElapsed } from "./widget.js";
import type { AgentWidgetState, WidgetState, BuilderScope } from "./types.js";

// ── Agent discovery (reuse pi's convention) ──────────────────────────

interface AgentDef {
  name: string;
  description: string;
  tools?: string[];
  model?: string;
  systemPrompt: string;
}

function loadSwarmAgents(): Map<string, AgentDef> {
  const agents = new Map<string, AgentDef>();

  // Load from the pi-swarm package's agents/ directory
  const pkgAgentsDir = path.resolve(__dirname, "../../agents");
  loadAgentsFrom(pkgAgentsDir, agents);

  // Load from user's ~/.pi/agent/agents/ (overrides package agents)
  const userAgentsDir = path.join(getAgentDir(), "agents");
  loadAgentsFrom(userAgentsDir, agents);

  // Load from project's .pi/agents/ (overrides both)
  const projectAgentsDir = path.join(process.cwd(), ".pi", "agents");
  loadAgentsFrom(projectAgentsDir, agents);

  return agents;
}

function loadAgentsFrom(dir: string, agents: Map<string, AgentDef>): void {
  if (!fs.existsSync(dir)) return;
  for (const file of fs.readdirSync(dir)) {
    if (!file.endsWith(".md")) continue;
    const content = fs.readFileSync(path.join(dir, file), "utf-8");
    const { frontmatter, body } = parseFrontmatter<Record<string, string>>(content);
    if (frontmatter.name) {
      agents.set(frontmatter.name, {
        name: frontmatter.name,
        description: frontmatter.description ?? "",
        tools: frontmatter.tools?.split(",").map((t: string) => t.trim()).filter(Boolean),
        model: frontmatter.model,
        systemPrompt: body,
      });
    }
  }
}

// ── Headless agent runner ────────────────────────────────────────────

interface RunResult {
  exitCode: number;
  messages: Message[];
  stderr: string;
  usage: UsageStats;
  model?: string;
  stopReason?: string;
  errorMessage?: string;
}

function writePromptTempFile(name: string, prompt: string): { dir: string; filePath: string } {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-swarm-"));
  const filePath = path.join(tmpDir, `prompt-${name.replace(/[^\w.-]+/g, "_")}.md`);
  fs.writeFileSync(filePath, prompt, { encoding: "utf-8", mode: 0o600 });
  return { dir: tmpDir, filePath };
}

async function runAgent(
  agentDef: AgentDef,
  task: string,
  cwd: string,
  signal?: AbortSignal,
  onMessage?: (messages: Message[], usage: UsageStats) => void,
): Promise<RunResult> {
  const args: string[] = ["--mode", "json", "-p", "--no-session"];
  if (agentDef.model) args.push("--model", agentDef.model);
  if (agentDef.tools?.length) args.push("--tools", agentDef.tools.join(","));

  let tmpDir: string | null = null;
  let tmpFile: string | null = null;

  const result: RunResult = {
    exitCode: 0,
    messages: [],
    stderr: "",
    usage: emptyUsage(),
  };

  try {
    if (agentDef.systemPrompt.trim()) {
      const tmp = writePromptTempFile(agentDef.name, agentDef.systemPrompt);
      tmpDir = tmp.dir;
      tmpFile = tmp.filePath;
      args.push("--append-system-prompt", tmpFile);
    }

    args.push(`Task: ${task}`);

    const exitCode = await new Promise<number>((resolve) => {
      const proc = spawn("pi", args, {
        cwd,
        shell: false,
        stdio: ["ignore", "pipe", "pipe"],
      });
      let buffer = "";

      const processLine = (line: string) => {
        if (!line.trim()) return;
        let event: any;
        try { event = JSON.parse(line); } catch { return; }

        if (event.type === "message_end" && event.message) {
          const msg = event.message as Message;
          result.messages.push(msg);
          if (msg.role === "assistant") {
            result.usage.turns++;
            const u = msg.usage;
            if (u) {
              result.usage.input += u.input || 0;
              result.usage.output += u.output || 0;
              result.usage.cacheRead += u.cacheRead || 0;
              result.usage.cacheWrite += u.cacheWrite || 0;
              result.usage.cost += u.cost?.total || 0;
              result.usage.contextTokens = u.totalTokens || 0;
            }
            if (!result.model && msg.model) result.model = msg.model;
            if (msg.stopReason) result.stopReason = msg.stopReason;
            if (msg.errorMessage) result.errorMessage = msg.errorMessage;
          }
          onMessage?.(result.messages, result.usage);
        }

        if (event.type === "tool_result_end" && event.message) {
          result.messages.push(event.message as Message);
          onMessage?.(result.messages, result.usage);
        }
      };

      proc.stdout.on("data", (data) => {
        buffer += data.toString();
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) processLine(line);
      });

      proc.stderr.on("data", (data) => { result.stderr += data.toString(); });
      proc.on("close", (code) => {
        if (buffer.trim()) processLine(buffer);
        resolve(code ?? 0);
      });
      proc.on("error", () => resolve(1));

      if (signal) {
        const kill = () => { proc.kill("SIGTERM"); setTimeout(() => { if (!proc.killed) proc.kill("SIGKILL"); }, 5000); };
        if (signal.aborted) kill();
        else signal.addEventListener("abort", kill, { once: true });
      }
    });

    result.exitCode = exitCode;
    return result;
  } finally {
    if (tmpFile) try { fs.unlinkSync(tmpFile); } catch {}
    if (tmpDir) try { fs.rmdirSync(tmpDir); } catch {}
  }
}

function getFinalOutput(messages: Message[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role === "assistant") {
      for (const part of msg.content) {
        if (part.type === "text") return part.text;
      }
    }
  }
  return "";
}

function formatTokens(n: number): string {
  if (n < 1000) return n.toString();
  if (n < 10000) return `${(n / 1000).toFixed(1)}k`;
  if (n < 1000000) return `${Math.round(n / 1000)}k`;
  return `${(n / 1000000).toFixed(1)}M`;
}

function formatUsage(u: UsageStats, model?: string): string {
  const parts: string[] = [];
  if (u.turns) parts.push(`${u.turns} turn${u.turns > 1 ? "s" : ""}`);
  if (u.input) parts.push(`↑${formatTokens(u.input)}`);
  if (u.output) parts.push(`↓${formatTokens(u.output)}`);
  if (u.cost) parts.push(`$${u.cost.toFixed(4)}`);
  if (model) parts.push(model);
  return parts.join(" ");
}

function aggregateUsage(agents: SwarmAgent[]): UsageStats {
  const total = emptyUsage();
  for (const a of agents) {
    total.input += a.usage.input;
    total.output += a.usage.output;
    total.cacheRead += a.usage.cacheRead;
    total.cacheWrite += a.usage.cacheWrite;
    total.cost += a.usage.cost;
    total.turns += a.usage.turns;
  }
  return total;
}

/** Format a tool call as a human-readable summary for activity logs. */
function formatToolSummary(name: string, input: Record<string, unknown> | undefined): string {
  if (!input) return name;
  const path = input.path ?? input.file_path;
  if (typeof path === "string") return `${name} ${path}`;
  if (typeof input.command === "string") return `${name} ${String(input.command).slice(0, 60)}`;
  if (typeof input.pattern === "string") return `${name} "${String(input.pattern).slice(0, 40)}"`;
  return name;
}

// ── Concurrency helper ───────────────────────────────────────────────

async function mapConcurrent<T, R>(
  items: T[],
  concurrency: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let nextIndex = 0;
  const workers = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (true) {
      const i = nextIndex++;
      if (i >= items.length) return;
      results[i] = await fn(items[i], i);
    }
  });
  await Promise.all(workers);
  return results;
}

// ── Main extension ───────────────────────────────────────────────────

export default function (pi: ExtensionAPI) {

  // ═══ Automatic swarm assessment — injected into every turn ══════════
  //
  // This is the key piece: the LLM is told at the system prompt level
  // to assess whether a task warrants swarm execution. It doesn't need
  // to be asked — it evaluates automatically.

  const SWARM_SYSTEM_PROMPT = `
## Swarm — Automatic Parallel Execution Assessment

You have access to a code modification swarm (tools: swarm, swarm_plan, swarm_merge, swarm_status, swarm_cleanup).
**Assess EVERY coding task** against these criteria and use the swarm automatically when warranted.
Do NOT wait to be told to use it.

### USE swarm when ALL of these are true:
- The project is a git repository
- The task modifies 4+ files across different modules/directories
- The changes are parallelizable (not strictly sequential)
- Examples: "refactor X across the codebase", "add error handling to all endpoints",
  "migrate from library A to B", "add tests for modules X, Y, Z",
  "implement feature spanning auth + api + db + tests"

### Do NOT use swarm when ANY of these are true:
- Task touches 1-3 files in a focused area (just do it directly — faster, cheaper)
- Changes are inherently sequential (each step depends on the previous)
- The user is asking a question, not requesting code changes
- The project is not a git repository
- You're unsure what files are involved (explore first, THEN decide)

### Decision flow:
1. Read the user's request
2. If it's clearly a small/focused change → do it directly
3. If it might be large → quickly check the scope (grep/find/ls to estimate files)
4. If 4+ files across different areas → call swarm_plan first to preview
5. If the plan looks right → run swarm
6. If the plan is overkill → do it directly instead

### Cost awareness:
- Scout (haiku): ~$0.15 | Builder (sonnet): ~$0.80 | Reviewer: ~$0.80
- A 3-agent swarm costs ~$1.75. Only worth it if it saves significant time.
- For 4-6 files: 1 scout + 1-2 builders. For 7+: full swarm with reviewer.
`.trim();

  // Only inject swarm instructions when we're inside a git repo.
  // No point telling the LLM about worktree-based parallelism outside of git.
  let isGitRepo: boolean | null = null;

  pi.on("before_agent_start", async (event, ctx) => {
    // Cache the git check — only run once per session
    if (isGitRepo === null) {
      const result = await pi.exec("git", ["rev-parse", "--is-inside-work-tree"], { cwd: ctx.cwd });
      isGitRepo = result.code === 0 && result.stdout.trim() === "true";
    }

    if (!isGitRepo) return; // Not a git repo — swarm tools won't work anyway

    return {
      systemPrompt: event.systemPrompt + "\n\n" + SWARM_SYSTEM_PROMPT,
    };
  });

  // ═══ swarm_plan — analyze and propose ═══════════════════════════════

  pi.registerTool({
    name: "swarm_plan",
    label: "Swarm Plan",
    description: [
      "Analyze task complexity and propose an agent allocation plan.",
      "Returns: complexity level, agent count, estimated cost, and reasoning.",
      "Call this AUTOMATICALLY before swarm when you estimate a task touches 4+ files.",
      "If the plan shows 'simple', skip swarm and do the work directly instead.",
    ].join(" "),
    parameters: Type.Object({
      objective: Type.String({ description: "What needs to be done" }),
      files: Type.Optional(Type.Array(Type.String(), { description: "File paths to modify (helps estimate complexity)" })),
      maxConcurrent: Type.Optional(Type.Number({ description: "Max simultaneous agents (default: 6)" })),
      maxBudgetUsd: Type.Optional(Type.Number({ description: "Max cost budget in USD (default: 15)" })),
    }),
    async execute(_toolCallId, params) {
      const config: SwarmConfig = {
        ...DEFAULT_SWARM_CONFIG,
        ...(params.maxConcurrent !== undefined ? { maxConcurrent: params.maxConcurrent } : {}),
        ...(params.maxBudgetUsd !== undefined ? { maxBudgetUsd: params.maxBudgetUsd } : {}),
      };

      const plan = planSwarm(params.objective, params.files ?? [], config);

      let text = `## Swarm Plan\n\n`;
      text += `**Complexity:** ${plan.complexity} | **Agents:** ${plan.agents.length} | **Est. cost:** $${plan.estimatedCost.toFixed(2)}\n\n`;
      text += `**Reasoning:** ${plan.reasoning}\n\n`;
      text += `### Agents\n`;
      for (const agent of plan.agents) {
        const deps = agent.dependsOn?.length ? ` (after: ${agent.dependsOn.join(", ")})` : "";
        const scope = agent.files?.length ? ` [${agent.files.join(", ")}]` : "";
        text += `- **${agent.name}** (${agent.capability})${deps}${scope}\n`;
        text += `  ${agent.task.slice(0, 120)}${agent.task.length > 120 ? "..." : ""}\n`;
      }

      return {
        content: [{ type: "text", text }],
        details: plan,
      };
    },

    renderResult(result, { expanded }, theme) {
      const plan = result.details as SwarmPlan | undefined;
      if (!plan) return new Text(result.content[0]?.type === "text" ? result.content[0].text : "", 0, 0);

      const icon = plan.complexity === "simple" ? "●" : plan.complexity === "moderate" ? "◐" : "◉";
      const color = plan.complexity === "simple" ? "success" : plan.complexity === "moderate" ? "warning" : "error";
      let text = theme.fg(color as any, icon) + " " + theme.fg("toolTitle", theme.bold("swarm plan "));
      text += theme.fg("accent", plan.complexity) + " ";
      text += theme.fg("dim", `${plan.agents.length} agents, ~$${plan.estimatedCost.toFixed(2)}`);

      if (expanded) {
        text += "\n" + theme.fg("dim", plan.reasoning);
        for (const a of plan.agents) {
          const deps = a.dependsOn?.length ? theme.fg("muted", ` → after ${a.dependsOn.join(", ")}`) : "";
          text += "\n  " + theme.fg("accent", a.name) + " " + theme.fg("muted", `(${a.capability})`) + deps;
        }
      }

      return new Text(text, 0, 0);
    },
  });

  // ═══ swarm — full orchestrated run ══════════════════════════════════

  pi.registerTool({
    name: "swarm",
    label: "Swarm",
    description: [
      "Run a full parallel code modification swarm: plan → scout → build → review → merge.",
      "Creates git worktrees for builders, deploys guard extensions, runs agents in parallel,",
      "and merges results back. Complexity-aware: auto-determines agent count based on task",
      "and budget. Each builder is isolated in its own worktree — no file conflicts.",
      "USE THIS AUTOMATICALLY when a task modifies 4+ files across different modules.",
      "Call swarm_plan first to preview, then swarm to execute.",
    ].join(" "),
    parameters: Type.Object({
      objective: Type.String({ description: "What needs to be done (the overall task)" }),
      files: Type.Optional(Type.Array(Type.String(), { description: "File paths to modify (helps with planning and scoping)" })),
      maxConcurrent: Type.Optional(Type.Number({ description: "Max simultaneous agents (default: 6)" })),
      maxBudgetUsd: Type.Optional(Type.Number({ description: "Max cost budget in USD (default: 15)" })),
      builderModel: Type.Optional(Type.String({ description: "Model for builders (default: claude-sonnet-4-6)" })),
      scoutModel: Type.Optional(Type.String({ description: "Model for scouts (default: claude-haiku-4-5)" })),
      skipScout: Type.Optional(Type.Boolean({ description: "Skip scout phase (use when you already have context)" })),
      skipReview: Type.Optional(Type.Boolean({ description: "Skip reviewer phase" })),
      autoMerge: Type.Optional(Type.Boolean({ description: "Auto-merge branches on success (default: true)" })),
      dryRun: Type.Optional(Type.Boolean({ description: "Plan only, don't spawn agents" })),
      testCommand: Type.Optional(Type.String({ description: "Shell command to run after all merges succeed (e.g. 'npm test'). Overrides config.postMergeTestCommand." })),
    }),

    async execute(_toolCallId, params, signal, onUpdate, ctx: any) {
      const config: SwarmConfig = {
        ...DEFAULT_SWARM_CONFIG,
        ...(params.maxConcurrent !== undefined ? { maxConcurrent: params.maxConcurrent } : {}),
        ...(params.maxBudgetUsd !== undefined ? { maxBudgetUsd: params.maxBudgetUsd } : {}),
        ...(params.builderModel ? { defaultBuilderModel: params.builderModel } : {}),
        ...(params.scoutModel ? { defaultScoutModel: params.scoutModel } : {}),
      };

      const repoRoot = await getRepoRoot(pi, process.cwd());
      if (!repoRoot) {
        return { content: [{ type: "text", text: "Error: not in a git repository. Swarm requires git for worktree isolation." }], isError: true };
      }

      const baseBranch = await getCurrentBranch(pi, repoRoot);

      // ── Auto-cleanup stale worktrees & orphaned branches (#5) ──────────
      const staleWorktrees = await listWorktrees(pi, repoRoot);
      let staleCleaned = 0;
      if (staleWorktrees.length > 0) {
        staleCleaned = await cleanupWorktrees(pi, repoRoot);
      }
      // Delete orphaned swarm/* branches (no matching worktree)
      const activeBranchSet = new Set(staleWorktrees.map((wt) => wt.branch));
      const allSwarmBranchResult = await pi.exec("git", ["branch", "--list", "swarm/*"], { cwd: repoRoot });
      const orphanedBranches = allSwarmBranchResult.stdout
        .split("\n")
        .map((l: string) => l.trim().replace(/^\*\s*/, ""))
        .filter((b: string) => b && !activeBranchSet.has(b));
      let orphansCleaned = 0;
      for (const branch of orphanedBranches) {
        const del = await pi.exec("git", ["branch", "-D", branch], { cwd: repoRoot });
        if (del.code === 0) orphansCleaned++;
      }

      // Check for dirty working tree
      const statusResult = await pi.exec("git", ["status", "--porcelain"], { cwd: repoRoot });
      if (statusResult.stdout.trim()) {
        return {
          content: [{ type: "text", text: "Error: working tree has uncommitted changes. Commit or stash before running swarm.\n\n" + statusResult.stdout }],
          isError: true,
        };
      }

      // ── Initialize mail store for inter-agent messaging ──
      const mailPath = getMailPath(repoRoot);
      initMailStore(mailPath);

      // ── Phase 0: Plan ──
      let plan = planSwarm(params.objective, params.files ?? [], config);

      // Auto-assessment guardrail: if the planner says "simple", tell the LLM
      // to just do the work directly — saves cost and time vs. spawning agents.
      if (plan.complexity === "simple" && plan.agents.length === 1 && !params.dryRun) {
        return {
          content: [{
            type: "text",
            text: `Swarm assessment: **simple** task (${plan.reasoning}). `
              + `Only 1 agent would be spawned — it's faster and cheaper to do this directly. `
              + `Proceed with the implementation yourself instead of using swarm.`,

          }],
          details: { plan, skipped: true, reason: "simple-task" },
        };
      }

      // Apply user overrides
      if (params.skipScout) {
        plan.agents = plan.agents.filter((a) => a.capability !== "scout");
        // Remove scout dependencies
        for (const a of plan.agents) {
          a.dependsOn = a.dependsOn?.filter((d) => !d.startsWith("scout"));
        }
      }
      if (params.skipReview) {
        plan.agents = plan.agents.filter((a) => a.capability !== "reviewer");
      }

      if (params.dryRun) {
        let text = `## Dry Run — Swarm Plan\n\n`;
        text += `**Complexity:** ${plan.complexity} | **Agents:** ${plan.agents.length} | **Est. cost:** $${plan.estimatedCost.toFixed(2)}\n`;
        text += `**Base branch:** ${baseBranch}\n\n`;
        for (const a of plan.agents) {
          text += `- ${a.name} (${a.capability})${a.dependsOn?.length ? ` after ${a.dependsOn.join(",")}` : ""}\n`;
        }
        return { content: [{ type: "text", text }], details: { plan, dryRun: true } };
      }

      // Load agent definitions
      const agentDefs = loadSwarmAgents();
      const agents: SwarmAgent[] = [];

      // Resolve phases: group by dependency layers
      const phases = resolvePhases(plan.agents);

      const autoMerge = params.autoMerge !== false;
      const allResults: SwarmAgent[] = [];
      const agentOutputs = new Map<string, string>(); // name → last output snippet
      // Budget kill switch (#6): shared flag + abort controller for all in-flight agents
      let budgetExceeded = false;
      const budgetAbortCtrl = new AbortController();

      // Persistent state for dashboard
      const runId = `run-${Date.now().toString(36)}`;
      const runState: SwarmRunState = {
        runId,
        objective: params.objective,
        status: "planning",
        complexity: plan.complexity,
        baseBranch,
        repoRoot,
        startedAt: new Date().toISOString(),
        completedAt: null,
        agents: [],
        mergeResults: [],
        totalUsage: emptyUsage(),
        planReasoning: plan.reasoning,
        updatedAt: new Date().toISOString(),
        phases: [],
        activityFeed: [],
        tokenRates: [],
        filesChanged: [],
      };
      writeState(repoRoot, runState);

      const emitStatus = () => {
        // Update in-memory state
        const done = allResults.filter((a) => a.status === "completed" || a.status === "failed").length;
        const running = allResults.filter((a) => a.status === "running").length;
        const total = allResults.length;
        const usage = aggregateUsage(allResults);

        // Persist to disk for dashboard
        runState.agents = allResults.map((a) => agentToState(a, agentOutputs.get(a.name)));
        runState.totalUsage = usage;

        // Sample token rate every ~5 seconds for sparkline
        const lastRate = runState.tokenRates?.slice(-1)[0];
        const shouldSample = !lastRate || Date.now() - new Date(lastRate.timestamp).getTime() > 5000;
        if (shouldSample) recordTokenRate(runState);

        writeState(repoRoot, runState);

        // Render inline TUI widget if UI is available
        if (ctx?.hasUI) {
          const widgetAgents = allResults.map((a) =>
            agentToWidgetState(a, agentOutputs.get(a.name)?.split("\n").pop()),
          );
          const widgetState = {
            agents: widgetAgents,
            totalCost: usage.cost,
            startedAt: runState.startedAt,
            completedAt: null as string | null,
            phase: runState.status,
          };
          ctx.ui.setWidget("swarm-progress", renderAgentWidget(widgetState as any, 80, undefined));
        }

        if (!onUpdate) return;
        onUpdate({
          content: [{ type: "text", text: `Swarm: ${done}/${total} done, ${running} running — $${usage.cost.toFixed(4)}` }],
          details: { plan, agents: allResults, baseBranch },
        });
      };

      // ── Execute phases sequentially, agents within a phase in parallel ──
      let scoutContext = ""; // accumulated scout findings for builders
      let builderContext = ""; // accumulated builder outputs for later-phase builders (#12)
      let phaseIndex = 0;

      for (const phase of phases) {
        // Create SwarmAgent entries for this phase
        const phaseAgents: SwarmAgent[] = [];
        // Phase record (populated below, completedAt set after agents finish)
        let phaseRecord: { name: string; capability: string; startedAt: string; completedAt: string | null; agents: string[] } | null = null;

        for (const planned of phase) {
          const capability = planned.capability as AgentCapability;
          const defName = capability === "builder" ? "swarm-builder" : capability === "reviewer" ? "swarm-reviewer" : `swarm-${capability}`;
          const agentDef = agentDefs.get(defName) ?? agentDefs.get(capability);

          if (!agentDef) {
            phaseAgents.push({
              name: planned.name,
              capability,
              task: planned.task,
              worktree: null,
              status: "failed",
              exitCode: 1,
              startedAt: null,
              completedAt: null,
              usage: emptyUsage(),
            });
            continue;
          }

          // Create worktree for builders
          let worktree = null;
          if (capability === "builder") {
            try {
              worktree = await createWorktree(pi, repoRoot, planned.name, baseBranch);
              // 3d: Pass scope to deployGuard for directory-boundary enforcement
              deployGuard(planned.name, capability, worktree.path, planned.files, planned.scope);
              deployMailExtension(worktree.path, planned.name, mailPath);
            } catch (err: any) {
              phaseAgents.push({
                name: planned.name,
                capability,
                task: planned.task,
                worktree: null,
                status: "failed",
                exitCode: 1,
                startedAt: null,
                completedAt: null,
                usage: emptyUsage(),
              });
              continue;
            }
          }

          phaseAgents.push({
            name: planned.name,
            capability,
            task: planned.task,
            worktree,
            status: "pending",
            exitCode: null,
            startedAt: null,
            completedAt: null,
            usage: emptyUsage(),
            model: planned.model ?? agentDef.model,
            files: planned.files,
            scope: planned.scope,
          });
        }

        allResults.push(...phaseAgents);

        // Update run phase status for dashboard
        const phaseCapabilities = new Set(phaseAgents.map((a) => a.capability));
        const primaryCap = phaseCapabilities.has("scout") ? "scout"
          : phaseCapabilities.has("builder") ? "builder"
          : phaseCapabilities.has("reviewer") ? "reviewer"
          : "planner";
        if (phaseCapabilities.has("scout")) runState.status = "scouting";
        else if (phaseCapabilities.has("builder")) runState.status = "building";
        else if (phaseCapabilities.has("reviewer")) runState.status = "reviewing";

        // Record phase start
        phaseRecord = {
          name: `phase-${phaseIndex + 1}`,
          capability: primaryCap,
          startedAt: new Date().toISOString(),
          completedAt: null,
          agents: phaseAgents.map((a) => a.name),
        };
        runState.phases = runState.phases ?? [];
        runState.phases.push(phaseRecord);

        emitStatus();

        // ── Reviewer blind fix (#1): create octopus-merge review worktree ──
        let reviewWorktree: WorktreeInfo | null = null;
        if (phaseCapabilities.has("reviewer")) {
          const successfulBuilderBranches = allResults
            .filter((a) => a.capability === "builder" && a.status === "completed" && a.worktree)
            .map((a) => a.worktree!.branch);
          if (successfulBuilderBranches.length > 0) {
            try {
              reviewWorktree = await createReviewWorktree(pi, repoRoot, successfulBuilderBranches, baseBranch);
            } catch {
              // Fall back: reviewer runs in repoRoot (partial visibility)
            }
          }
        }

        // Run this phase's agents in parallel
        const runnable = phaseAgents.filter((a) => a.status === "pending");

        await mapConcurrent(runnable, config.maxConcurrent, async (agent) => {
          // ── Budget kill switch: skip if over budget ──────────────────────
          if (budgetExceeded) {
            agent.status = "failed";
            agent.failReason = "budget exceeded";
            agent.completedAt = new Date().toISOString();
            emitStatus();
            return;
          }

          agent.status = "running";
          agent.startedAt = new Date().toISOString();
          emitStatus();

          const defName = agent.capability === "builder" ? "swarm-builder" : agent.capability === "reviewer" ? "swarm-reviewer" : `swarm-${agent.capability}`;
          const agentDef = agentDefs.get(defName) ?? agentDefs.get(agent.capability)!;

          // ── Scout context as file (#9) ──────────────────────────────────
          // Builders: write scoutContext to .pi-swarm/scout-context.md in their worktree
          // Reviewers: prepend as plain text (they run read-only on the review worktree)
          let fullTask = agent.task;
          if (scoutContext && agent.capability === "builder" && agent.worktree) {
            const scoutDir = path.join(agent.worktree.path, ".pi-swarm");
            fs.mkdirSync(scoutDir, { recursive: true });
            fs.writeFileSync(path.join(scoutDir, "scout-context.md"), scoutContext, "utf-8");
            fullTask = `Scout findings are in \`.pi-swarm/scout-context.md\` — read it with the read tool before starting.`;
            // Write prior-phase builder context if available (#12)
            if (builderContext) {
              fs.writeFileSync(path.join(scoutDir, "builder-context.md"), builderContext, "utf-8");
              fullTask += `\n\nPrior builders' work is in \`.pi-swarm/builder-context.md\` — read it to see what APIs/functions they created that you should import (not recreate).`;
            }
            fullTask += `\n\n---\n\nTask: ${agent.task}`;
          } else if (scoutContext && agent.capability === "reviewer") {
            fullTask = `Context from scouts:\n\n${scoutContext}\n\n---\n\nTask: ${agent.task}`;
          }

          // Reviewer runs in the review worktree so it sees all builder changes
          const cwd = (agent.capability === "reviewer" && reviewWorktree)
            ? reviewWorktree.path
            : (agent.worktree?.path ?? repoRoot);

          // ── Timeout wiring (#2) ─────────────────────────────────────────
          const timeoutMs = config.timeouts[agent.capability] ?? 0;
          const agentAbortCtrl = new AbortController();
          const abortCleanup: Array<() => void> = [];

          if (signal?.aborted) {
            agentAbortCtrl.abort();
          } else {
            if (signal) {
              const outerAbort = () => agentAbortCtrl.abort();
              signal.addEventListener("abort", outerAbort, { once: true });
              abortCleanup.push(() => signal.removeEventListener("abort", outerAbort));
            }
            const budgetAbort = () => agentAbortCtrl.abort();
            budgetAbortCtrl.signal.addEventListener("abort", budgetAbort, { once: true });
            abortCleanup.push(() => budgetAbortCtrl.signal.removeEventListener("abort", budgetAbort));
          }

          let timeoutHandle: ReturnType<typeof setTimeout> | null = null;
          if (timeoutMs > 0 && !agentAbortCtrl.signal.aborted) {
            timeoutHandle = setTimeout(() => {
              agent.failReason = "timeout";
              agentAbortCtrl.abort();
            }, timeoutMs);
          }

          try {
            const result = await runAgent(agentDef, fullTask, cwd, agentAbortCtrl.signal, (msgs, usage) => {
              agent.usage = { ...usage };
              // Capture last output for dashboard
              const lastMsg = getFinalOutput(msgs);
              if (lastMsg) agentOutputs.set(agent.name, lastMsg.slice(-500));

              // ── Rich tracking: tool calls, activity log, token rate ──
              const latestMsg = msgs[msgs.length - 1];
              const now = new Date().toISOString();

              if (latestMsg?.role === "assistant") {
                const prevTurn = agent.currentTurn ?? 0;
                agent.currentTurn = usage.turns;

                // Live token throughput
                if (agent.startedAt) {
                  const sec = (Date.now() - new Date(agent.startedAt).getTime()) / 1000;
                  if (sec > 0) agent.tokensPerSecond = Math.round((usage.output / sec) * 10) / 10;
                }

                // Rough progress estimate based on expected turns per role
                const expectedTurns = agent.capability === "scout" ? 15
                  : agent.capability === "reviewer" ? 12 : 25;
                agent.progressPct = Math.min(95, Math.round((usage.turns / expectedTurns) * 100));

                // Extract tool uses from this assistant message
                for (const part of (latestMsg.content ?? []) as any[]) {
                  if (part.type === "tool_use") {
                    // Update per-agent tool call tally
                    agent.toolCalls = agent.toolCalls ?? [];
                    const tc = agent.toolCalls.find((t) => t.name === part.name);
                    if (tc) { tc.count++; tc.lastAt = now; }
                    else agent.toolCalls.push({ name: part.name, count: 1, lastAt: now });

                    // Per-agent activity log
                    const summary = formatToolSummary(part.name, part.input as Record<string, unknown>);
                    const entry = { timestamp: now, type: "tool_call" as const, summary };
                    agent.activityLog = agent.activityLog ?? [];
                    agent.activityLog.push(entry);
                    if (agent.activityLog.length > 20) agent.activityLog = agent.activityLog.slice(-20);

                    // Global activity feed
                    appendActivity(runState, { ...entry, agent: agent.name });
                  }
                }

                // Log turn completion when a new turn starts
                if (usage.turns > prevTurn) {
                  const turnEntry = {
                    timestamp: now,
                    type: "turn_complete" as const,
                    summary: `turn ${usage.turns}`,
                  };
                  agent.activityLog = agent.activityLog ?? [];
                  agent.activityLog.push(turnEntry);
                  if (agent.activityLog.length > 20) agent.activityLog = agent.activityLog.slice(-20);
                  appendActivity(runState, { ...turnEntry, agent: agent.name });
                }
              }

              emitStatus();
            });

            agent.exitCode = result.exitCode;
            agent.usage = result.usage;
            agent.model = result.model;
            agent.completedAt = new Date().toISOString();
            agent.status = result.exitCode === 0 ? "completed" : "failed";

            // Collect scout output for next phases
            if (agent.capability === "scout" && result.exitCode === 0) {
              const output = getFinalOutput(result.messages);
              if (output) scoutContext += `\n\n### ${agent.name}\n${output}`;
            }

            // Collect builder output for later-phase builders (#12)
            if (agent.capability === "builder" && agent.status === "completed") {
              const output = getFinalOutput(result.messages);
              const filesOwned = agent.files?.join(", ") ?? "(unknown)";
              if (output) {
                builderContext += `\n\n### ${agent.name} (files: ${filesOwned})\n${output}`;
              }
            }

            // For builders: commit work and apply git-state-based completion logic (#3e)
            if (agent.capability === "builder" && agent.worktree) {
              await pi.exec("git", ["add", "-A"], { cwd: agent.worktree.path });
              await pi.exec("git", ["commit", "-m", `swarm: ${agent.name} — ${params.objective.slice(0, 72)}`], { cwd: agent.worktree.path });

              // Check for commits beyond base branch (git-state-based, not output-text-based)
              const logResult = await pi.exec("git", ["log", `${baseBranch}..HEAD`, "--oneline"], { cwd: agent.worktree.path });
              const hasWork = logResult.code === 0 && logResult.stdout.trim().length > 0;

              // Git-state-based completion detection — no longer depends on "### Completed" headers
              if (result.exitCode === 0 && hasWork) {
                agent.status = "completed";
              } else if (result.exitCode === 0 && !hasWork) {
                agent.status = "completed"; // Clean exit, no changes needed
              } else if (hasWork) {
                // Non-zero exit but has commits — salvage useful work
                agent.status = "completed";
              } else {
                agent.status = "failed";
                if (!agent.failReason) {
                  agent.failReason = "no commits produced";
                }
              }

              // Populate diagnostics for visibility into agent outcomes
              agent.diagnostics = {
                exitCode: result.exitCode,
                hasCommits: hasWork,
                changedFiles: logResult.stdout.trim().split("\n").filter(Boolean),
                scopeViolations: [],
                stderrTail: result.stderr.slice(-500),
                lastToolCall: "",
                turnsCompleted: result.usage.turns,
              };

              // Collect file diff stats for dashboard
              const diffResult = await pi.exec(
                "git", ["diff", "--numstat", `${baseBranch}...HEAD`],
                { cwd: agent.worktree.path },
              );
              if (diffResult.code === 0) {
                const filesModified: { path: string; linesAdded: number; linesRemoved: number }[] = [];
                for (const line of diffResult.stdout.split("\n")) {
                  const parts = line.trim().split("\t");
                  if (parts.length >= 3) {
                    const linesAdded = parseInt(parts[0], 10) || 0;
                    const linesRemoved = parseInt(parts[1], 10) || 0;
                    const filePath = parts[2];
                    if (filePath) filesModified.push({ path: filePath, linesAdded, linesRemoved });
                  }
                }
                agent.filesModified = filesModified;
                agent.progressPct = 100;

                runState.filesChanged = runState.filesChanged ?? [];
                for (const fm of filesModified) {
                  const existing = runState.filesChanged.find(
                    (fc) => fc.path === fm.path && fc.agent === agent.name,
                  );
                  if (existing) {
                    existing.linesAdded = fm.linesAdded;
                    existing.linesRemoved = fm.linesRemoved;
                  } else {
                    runState.filesChanged.push({ ...fm, agent: agent.name });
                  }
                }
              }

              // ── Per-agent retry (#7) ──────────────────────────────────────
              if (agent.status === "failed" && agent.failReason !== "timeout" && agent.failReason !== "budget exceeded") {
                const attempt = agent.attempt ?? 0;
                if (attempt < config.maxRetries) {
                  try {
                    // Remove failed worktree, create fresh one for retry
                    await pi.exec("git", ["worktree", "remove", "--force", agent.worktree.path], { cwd: repoRoot });
                    const retryWorktree = await createWorktree(pi, repoRoot, agent.name, baseBranch);
                    // 3d: Pass scope to deployGuard on retry too
                    deployGuard(agent.name, "builder", retryWorktree.path, agent.files, agent.scope);
                    deployMailExtension(retryWorktree.path, agent.name, mailPath);
                    agent.worktree = retryWorktree;
                    agent.attempt = attempt + 1;
                    agent.status = "running";
                    agent.exitCode = null;
                    agent.startedAt = new Date().toISOString();
                    agent.completedAt = null;
                    agent.failReason = undefined;
                    emitStatus();

                    // Write scout + builder context files into the retry worktree
                    const retryScoutDir = path.join(retryWorktree.path, ".pi-swarm");
                    fs.mkdirSync(retryScoutDir, { recursive: true });
                    if (scoutContext) {
                      fs.writeFileSync(path.join(retryScoutDir, "scout-context.md"), scoutContext, "utf-8");
                    }
                    if (builderContext) {
                      fs.writeFileSync(path.join(retryScoutDir, "builder-context.md"), builderContext, "utf-8");
                    }

                    // 3f: Include failure context in retry task
                    let retryTask = `PREVIOUS ATTEMPT FAILED: ${agent.failReason || "unknown error"}`;
                    if (result.stderr) retryTask += `\nError output: ${result.stderr.slice(-300)}`;
                    retryTask += `\n\nPlease try again, avoiding the previous error.\n\n---\n\n`;
                    retryTask += `Scout findings are in \`.pi-swarm/scout-context.md\` — read it before starting.`;
                    if (builderContext) {
                      retryTask += `\n\nPrior builders' work is in \`.pi-swarm/builder-context.md\` — read it to see what APIs/functions they created that you should import (not recreate).`;
                    }
                    retryTask += `\n\n---\n\nTask: ${agent.task}`;

                    const retryResult = await runAgent(agentDef, retryTask, retryWorktree.path, agentAbortCtrl.signal);
                    agent.exitCode = retryResult.exitCode;
                    agent.usage.input += retryResult.usage.input;
                    agent.usage.output += retryResult.usage.output;
                    agent.usage.cost += retryResult.usage.cost;
                    agent.usage.turns += retryResult.usage.turns;
                    agent.model = retryResult.model ?? agent.model;
                    agent.completedAt = new Date().toISOString();

                    await pi.exec("git", ["add", "-A"], { cwd: retryWorktree.path });
                    await pi.exec("git", ["commit", "-m", `swarm: ${agent.name} retry-${agent.attempt} — ${params.objective.slice(0, 60)}`], { cwd: retryWorktree.path });

                    const retryLog = await pi.exec("git", ["log", `${baseBranch}..HEAD`, "--oneline"], { cwd: retryWorktree.path });
                    const retryHasWork = retryLog.code === 0 && retryLog.stdout.trim().length > 0;

                    // Git-state-based completion for retry as well
                    if (retryResult.exitCode === 0 || retryHasWork) {
                      agent.status = "completed";
                    } else {
                      agent.status = "failed";
                      agent.failReason = "no commits produced (retry)";
                    }
                  } catch {
                    agent.status = "failed";
                    agent.failReason = "retry failed";
                  }
                }
              }
            }
          } catch (err: any) {
            agent.exitCode = 1;
            agent.status = "failed";
            if (!agent.failReason) agent.failReason = String((err as any)?.message ?? "unknown error");
            agent.completedAt = new Date().toISOString();
          } finally {
            // Cleanup timeout and abort listeners
            if (timeoutHandle) clearTimeout(timeoutHandle);
            for (const clean of abortCleanup) clean();
          }

          // ── Budget kill switch: check after each agent completes ─────────
          {
            const currentCost = aggregateUsage(allResults).cost;
            if (!budgetExceeded && currentCost > config.maxBudgetUsd) {
              budgetExceeded = true;
              budgetAbortCtrl.abort();
            }
          }

          emitStatus();
        });

        // ── Cleanup review worktree after reviewer phase ──────────────────
        if (reviewWorktree) {
          await removeReviewWorktree(pi, repoRoot, reviewWorktree.path);
          reviewWorktree = null;
        }


        // Record phase completion
        if (phaseRecord) phaseRecord.completedAt = new Date().toISOString();
        phaseIndex++;

        // Stagger between phases
        if (config.staggerDelayMs > 0) {
          await new Promise((r) => setTimeout(r, config.staggerDelayMs));
        }
      }

      // ── Phase N+1: Merge ──
      runState.status = "merging";
      emitStatus();

      const mergeResults: MergeResult[] = [];
      const successfulBuilders = allResults.filter(
        (a) => a.capability === "builder" && a.status === "completed" && a.worktree,
      );

      if (autoMerge && successfulBuilders.length > 0) {
        const branches = successfulBuilders.map((a) => ({
          branch: a.worktree!.branch,
          agentName: a.name,
        }));

        const results = await mergeAll(pi, repoRoot, branches, baseBranch);
        mergeResults.push(...results);

        for (const mr of results) {
          const agent = allResults.find((a) => a.name === mr.agentName);
          if (agent) agent.status = mr.success ? "merged" : "merge_failed";
        }
      }

      // ── Cleanup worktrees ──
      const mergedAgents = new Set(allResults.filter((a) => a.status === "merged").map((a) => a.name));
      if (mergedAgents.size > 0) {
        await cleanupWorktrees(pi, repoRoot, mergedAgents);
      }

      // ── Post-merge tests (#8) ──────────────────────────────────────────
      const testCmd = (params as any).testCommand ?? config.postMergeTestCommand;
      let postMergeTestResult: { passed: boolean; output: string } | null = null;
      if (testCmd && mergeResults.some((r) => r.success)) {
        try {
          postMergeTestResult = await runPostMergeTests(pi, repoRoot, testCmd as string);
        } catch (err: any) {
          postMergeTestResult = { passed: false, output: String(err?.message ?? "test command failed") };
        }
      }

      // ── Summary ──
      const totalUsage = aggregateUsage(allResults);
      const merged = mergeResults.filter((r) => r.success).length;
      const conflicts = mergeResults.filter((r) => !r.success);

      let summary = `## Swarm Complete\n\n`;
      summary += `**Objective:** ${params.objective}\n`;
      summary += `**Complexity:** ${plan.complexity} | **Agents:** ${allResults.length} | **Cost:** $${totalUsage.cost.toFixed(4)}\n`;
      summary += `**Merged:** ${merged}/${successfulBuilders.length} branches\n`;
      if (staleCleaned > 0 || orphansCleaned > 0) {
        summary += `**Pre-run cleanup:** ${staleCleaned} stale worktree(s), ${orphansCleaned} orphaned branch(es) removed\n`;
      }
      if (budgetExceeded) {
        summary += `**⚠️ Budget exceeded:** $${config.maxBudgetUsd} limit hit — some agents were aborted\n`;
      }
      summary += `\n`;

      for (const agent of allResults) {
        const icon = agent.status === "merged" ? "✓"
          : agent.status === "completed" ? "●"
          : agent.status === "merge_failed" ? "⚠"
          : "✗";
        summary += `- ${icon} **${agent.name}** (${agent.capability}) — ${agent.status} — ${formatUsage(agent.usage, agent.model)}\n`;
      }

      if (conflicts.length > 0) {
        summary += `\n### Merge Conflicts\n`;
        for (const c of conflicts) {
          summary += `- **${c.agentName}** (${c.branch}): ${c.conflicts.join(", ")}\n`;
          summary += `  ${c.error}\n`;
        }
        summary += `\nResolve conflicts manually, then run \`swarm_merge\` to retry.\n`;
      }

      // ── Post-merge test results in summary ──
      if (postMergeTestResult) {
        const icon = postMergeTestResult.passed ? "✓" : "✗";
        summary += `\n### Post-merge Tests\n`;
        summary += `${icon} \`${testCmd}\` — ${postMergeTestResult.passed ? "passed" : "FAILED"}\n`;
        if (postMergeTestResult.output) {
          const lines = postMergeTestResult.output.split("\n").slice(0, 25);
          summary += `\`\`\`\n${lines.join("\n")}\n\`\`\`\n`;
        }
      }

      // ── Mail summary ──
      const allMail = listMail(mailPath);
      if (allMail.length > 0) {
        const apiMsgs = allMail.filter((m) => m.type === "api_created");
        const otherMsgs = allMail.filter((m) => m.type !== "api_created");
        summary += `\n### Inter-Agent Mail (${allMail.length} messages)\n`;
        if (apiMsgs.length > 0) {
          summary += `**APIs created:**\n`;
          for (const m of apiMsgs) {
            summary += `- ${m.from}: ${m.subject}\n`;
          }
        }
        if (otherMsgs.length > 0) {
          summary += `**Other messages:** ${otherMsgs.length} (${otherMsgs.map((m) => m.type).join(", ")})\n`;
        }
      }

      // ── Final state persistence ──
      // Only mark the overall run as "failed" if a build actually failed.
      // Merge failures are recoverable (swarm_merge can retry).
      const buildFailed = allResults.some((a) => a.status === "failed");
      runState.status = buildFailed ? "failed" : "completed";
      runState.completedAt = new Date().toISOString();
      runState.mergeResults = mergeResults;
      runState.totalUsage = totalUsage;
      runState.agents = allResults.map((a) => agentToState(a, agentOutputs.get(a.name)));
      writeState(repoRoot, runState);
      appendHistory(repoRoot, runState);

      // 3c: Clear inline widget on completion
      if (ctx?.hasUI) {
        ctx.ui.setWidget("swarm-progress", undefined);
      }

      return {
        content: [{ type: "text", text: summary }],
        details: { plan, agents: allResults, mergeResults, baseBranch, totalUsage },
      };
    },

    renderCall(args, theme) {
      let text = theme.fg("toolTitle", theme.bold("swarm "));
      const preview = args.objective.length > 60 ? args.objective.slice(0, 60) + "..." : args.objective;
      text += theme.fg("accent", preview);
      if (args.files?.length) text += theme.fg("dim", ` (${args.files.length} files)`);
      if (args.dryRun) text += theme.fg("warning", " [dry-run]");
      return new Text(text, 0, 0);
    },

    renderResult(result, { expanded }, theme) {
      const details = result.details as any;
      if (!details?.agents) {
        const text = result.content[0];
        return new Text(text?.type === "text" ? text.text : "", 0, 0);
      }

      const agents = details.agents as SwarmAgent[];
      const usage = details.totalUsage as UsageStats | undefined;
      const mergeResults = (details.mergeResults ?? []) as MergeResult[];
      const plan = details.plan as SwarmPlan | undefined;

      const merged = agents.filter((a) => a.status === "merged").length;
      const failed = agents.filter((a) => a.status === "failed").length;
      const running = agents.filter((a) => a.status === "running").length;

      const icon = running > 0 ? theme.fg("warning", "⏳") :
        failed > 0 ? theme.fg("warning", "◐") : theme.fg("success", "✓");

      let status = running > 0
        ? `${agents.length - running}/${agents.length} done, ${running} running`
        : `${agents.length} agents, ${merged} merged`;

      let text = `${icon} ${theme.fg("toolTitle", theme.bold("swarm "))}${theme.fg("accent", status)}`;

      if (plan) text += ` ${theme.fg("dim", `[${plan.complexity}]`)}`;

      for (const a of agents) {
        const aIcon = a.status === "merged" ? theme.fg("success", "✓") :
          a.status === "completed" ? theme.fg("accent", "●") :
          a.status === "merge_failed" ? theme.fg("warning", "⚠") :
          a.status === "running" ? theme.fg("warning", "⏳") :
          theme.fg("error", "✗");
        text += `\n  ${aIcon} ${theme.fg("accent", a.name)} ${theme.fg("muted", `(${a.capability})`)}`;
        if (a.usage.cost > 0) text += ` ${theme.fg("dim", `$${a.usage.cost.toFixed(4)}`)}`;
      }

      if (usage && usage.cost > 0) {
        text += `\n\n${theme.fg("dim", `Total: ${formatUsage(usage)}`)}`;
      }

      if (expanded && mergeResults.length > 0) {
        const conflicts = mergeResults.filter((r) => !r.success);
        if (conflicts.length > 0) {
          text += `\n\n${theme.fg("error", "Merge conflicts:")}`;
          for (const c of conflicts) {
            text += `\n  ${theme.fg("error", c.agentName)}: ${c.conflicts.join(", ")}`;
          }
        }
      }

      return new Text(text, 0, 0);
    },
  });

  // ═══ swarm_merge — merge remaining branches ═════════════════════════

  pi.registerTool({
    name: "swarm_merge",
    label: "Swarm Merge",
    description: "Merge swarm agent branches into the current branch. Use after resolving conflicts or for manual merge control.",
    parameters: Type.Object({
      branches: Type.Optional(Type.Array(Type.String(), { description: "Specific branch names to merge (default: all swarm/* branches)" })),
      dryRun: Type.Optional(Type.Boolean({ description: "Check mergeability without actually merging" })),
    }),
    async execute(_toolCallId, params) {
      const repoRoot = await getRepoRoot(pi, process.cwd());
      if (!repoRoot) return { content: [{ type: "text", text: "Not in a git repository." }], isError: true };

      const targetBranch = await getCurrentBranch(pi, repoRoot);
      const worktrees = await listWorktrees(pi, repoRoot);

      let branchesToMerge = worktrees.map((wt) => ({ branch: wt.branch, agentName: wt.agentName }));
      if (params.branches?.length) {
        branchesToMerge = branchesToMerge.filter((b) => params.branches!.includes(b.branch));
      }

      if (branchesToMerge.length === 0) {
        return { content: [{ type: "text", text: "No swarm branches to merge." }] };
      }

      if (params.dryRun) {
        let text = "## Merge Check\n\n";
        for (const { branch, agentName } of branchesToMerge) {
          const check = await checkMergeability(pi, repoRoot, branch, targetBranch);
          const icon = check.clean ? "✓" : "✗";
          text += `- ${icon} ${agentName} (${branch})${check.conflicts.length ? ": " + check.conflicts.join(", ") : ""}\n`;
        }
        return { content: [{ type: "text", text }] };
      }

      const results = await mergeAll(pi, repoRoot, branchesToMerge, targetBranch);
      let text = "## Merge Results\n\n";
      for (const r of results) {
        const icon = r.success ? "✓" : "✗";
        text += `- ${icon} ${r.agentName} (${r.branch})`;
        if (!r.success) text += ` — ${r.error}`;
        text += "\n";
      }

      // Cleanup merged worktrees
      const merged = new Set(results.filter((r) => r.success).map((r) => r.agentName));
      if (merged.size > 0) await cleanupWorktrees(pi, repoRoot, merged);

      return { content: [{ type: "text", text }], details: { results } };
    },
  });

  // ═══ swarm_status — show active state ═══════════════════════════════

  pi.registerTool({
    name: "swarm_status",
    label: "Swarm Status",
    description: "Show active swarm worktrees and branches.",
    parameters: Type.Object({}),
    async execute() {
      const repoRoot = await getRepoRoot(pi, process.cwd());
      if (!repoRoot) return { content: [{ type: "text", text: "Not in a git repository." }] };

      const worktrees = await listWorktrees(pi, repoRoot);
      if (worktrees.length === 0) {
        return { content: [{ type: "text", text: "No active swarm worktrees." }] };
      }

      let text = `## Swarm Worktrees (${worktrees.length})\n\n`;
      for (const wt of worktrees) {
        text += `- **${wt.agentName}**: ${wt.branch}\n  ${wt.path}\n`;
      }
      return { content: [{ type: "text", text }], details: { worktrees } };
    },
  });

  // ═══ swarm_cleanup — remove all worktrees ═══════════════════════════

  pi.registerTool({
    name: "swarm_cleanup",
    label: "Swarm Cleanup",
    description: "Remove all swarm worktrees and branches. Use after merging or to reset.",
    parameters: Type.Object({}),
    async execute() {
      const repoRoot = await getRepoRoot(pi, process.cwd());
      if (!repoRoot) return { content: [{ type: "text", text: "Not in a git repository." }] };

      const cleaned = await cleanupWorktrees(pi, repoRoot);
      return { content: [{ type: "text", text: cleaned > 0 ? `Cleaned ${cleaned} worktree(s).` : "No worktrees to clean." }] };
    },
  });

  // ═══ /swarm-dashboard command — launch dashboard in a tmux pane ═════

  pi.registerCommand("swarm-dashboard", {
    description: "Launch the swarm dashboard in a new tmux pane (split right)",
    handler: async (_args, ctx) => {
      const repoRoot = await getRepoRoot(pi, ctx.cwd);
      if (!repoRoot) {
        ctx.ui.notify("Not in a git repo — swarm dashboard requires git.", "error");
        return;
      }

      const dashboardScript = path.resolve(__dirname, "../../dashboard/index.mjs");
      const result = await pi.exec("tmux", [
        "split-window", "-h", "-d",
        "-c", repoRoot,
        "node", dashboardScript, repoRoot,
      ]);

      if (result.code === 0) {
        ctx.ui.notify("Dashboard opened in tmux pane (right split)", "info");
      } else {
        // Fallback: try running in a new tmux window
        const result2 = await pi.exec("tmux", [
          "new-window", "-d", "-n", "swarm-dash",
          "-c", repoRoot,
          "node", dashboardScript, repoRoot,
        ]);
        if (result2.code === 0) {
          ctx.ui.notify("Dashboard opened in new tmux window 'swarm-dash'", "info");
        } else {
          ctx.ui.notify(`Run manually: node ${dashboardScript} ${repoRoot}`, "warning");
        }
      }
    },
  });
}

// ── Phase resolver ───────────────────────────────────────────────────

/**
 * Topological sort of agents into execution phases.
 * Agents in the same phase can run in parallel.
 */
function resolvePhases(agents: PlannedAgent[]): PlannedAgent[][] {
  const phases: PlannedAgent[][] = [];
  const completed = new Set<string>();
  const remaining = [...agents];

  while (remaining.length > 0) {
    const phase: PlannedAgent[] = [];

    for (let i = remaining.length - 1; i >= 0; i--) {
      const agent = remaining[i];
      const deps = agent.dependsOn ?? [];
      if (deps.every((d) => completed.has(d))) {
        phase.push(agent);
        remaining.splice(i, 1);
      }
    }

    if (phase.length === 0) {
      // Cycle or unresolvable deps — dump everything into one phase
      phases.push(remaining.splice(0));
      break;
    }

    for (const a of phase) completed.add(a.name);
    phases.push(phase);
  }

  return phases;
}
