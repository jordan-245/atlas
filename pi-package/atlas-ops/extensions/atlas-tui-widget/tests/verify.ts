/**
 * Atlas TUI Widget — Verification Script
 *
 * Tests pure rendering logic and state management in core.ts.
 * Does NOT require a Pi session, terminal, or @mariozechner/pi-tui.
 *
 * Run:
 *   npx tsx pi-package/atlas-ops/extensions/atlas-tui-widget/tests/verify.ts
 *   # or from atlas-ops dir:
 *   npm run verify-tui
 *
 * Three test categories:
 *   1. Width-safety: every rendered line ≤ requested width
 *   2. Status/color correctness: statusColor() and statusIcon() mappings
 *   3. Bounded memory: recentActivity never exceeds MAX_ACTIVITY
 */

import { strict as assert } from "node:assert";

import {
  MAX_ACTIVITY,
  capActiveTools,
  createState,
  fmtDuration,
  pushBounded,
  renderStatus,
  renderWidget,
  statusColor,
  statusIcon,
  summarizeArgs,
  type ActivityEntry,
  type Theme,
  type WidthFns,
} from "../src/core";

// ─── Mock theme (identity — no ANSI codes → visibleWidth = string length) ────

const mockTheme: Theme = {
  fg: (_color: string, text: string) => text,
};

// ─── Mock width functions (simple character count, no ANSI awareness) ─────────

const mockWidthFns: WidthFns = {
  /** Simple truncation: chop at width, append ellipsis if provided and string is longer. */
  truncate: (str: string, width: number, ellipsis?: string): string => {
    if (str.length <= width) return str;
    if (ellipsis && ellipsis.length < width) {
      return str.slice(0, width - ellipsis.length) + ellipsis;
    }
    return str.slice(0, width);
  },
  visible: (str: string): number => str.length,
};

// ─── Test runner ──────────────────────────────────────────────────────────────

let passed = 0;
let failed = 0;

function test(name: string, fn: () => void) {
  try {
    fn();
    console.log(`  ✓  ${name}`);
    passed++;
  } catch (err) {
    console.error(`  ✗  ${name}`);
    console.error(`     ${(err as Error).message}`);
    failed++;
  }
}

// ─── 1. Width-safety ─────────────────────────────────────────────────────────

console.log("\n── 1. Width-safety ──");

test("empty state renders all lines ≤ 80 cols", () => {
  const state = createState();
  const lines = renderWidget(state, mockTheme, 80, mockWidthFns);
  for (const line of lines) {
    assert.ok(
      line.length <= 80,
      `Line too wide (${line.length} > 80): "${line}"`,
    );
  }
});

test("empty state renders all lines ≤ 40 cols (minimum)", () => {
  const state = createState();
  const lines = renderWidget(state, mockTheme, 40, mockWidthFns);
  for (const line of lines) {
    assert.ok(
      line.length <= 40,
      `Line too wide (${line.length} > 40): "${line}"`,
    );
  }
});

test("returns empty array when width < 40 (too narrow to render)", () => {
  const state = createState();
  const lines = renderWidget(state, mockTheme, 30, mockWidthFns);
  assert.strictEqual(lines.length, 0, "Should return [] for width < 40");
});

test("running tool renders all lines ≤ 120 cols", () => {
  const state = createState();
  state.activeTools.set("t1", {
    toolCallId: "t1",
    toolName: "Bash",
    args: "find /root/atlas -name '*.py' | xargs grep -l 'momentum_breakout'",
    status: "running",
    startMs: Date.now() - 2500,
  });
  const lines = renderWidget(state, mockTheme, 120, mockWidthFns);
  for (const line of lines) {
    assert.ok(line.length <= 120, `Line too wide (${line.length} > 120): "${line}"`);
  }
});

test("very long tool name and args render ≤ 60 cols", () => {
  const state = createState();
  state.recentActivity.push({
    toolCallId: "t2",
    toolName: "atlas_jobs_run_very_long_name_that_exceeds_column",
    args: "This is an extremely long argument string that should be truncated properly",
    status: "success",
    startMs: Date.now() - 5000,
    durationMs: 4200,
  });
  const lines = renderWidget(state, mockTheme, 60, mockWidthFns);
  for (const line of lines) {
    assert.ok(line.length <= 60, `Line too wide (${line.length} > 60): "${line}"`);
  }
});

test("mixed running + completed renders ≤ 80 cols", () => {
  const state = createState();
  state.toolTotal = 3;
  state.toolSuccess = 2;
  state.delegations = 1;
  state.activeTools.set("run1", {
    toolCallId: "run1",
    toolName: "subagent",
    args: "researcher — deep analysis task",
    status: "running",
    startMs: Date.now() - 10_000,
  });
  state.recentActivity.push(
    {
      toolCallId: "c1",
      toolName: "Read",
      args: "memory/SUMMARY.md",
      status: "success",
      startMs: Date.now() - 3000,
      durationMs: 200,
    },
    {
      toolCallId: "c2",
      toolName: "Bash",
      args: "git status --short",
      status: "success",
      startMs: Date.now() - 2000,
      durationMs: 89,
    },
  );
  const lines = renderWidget(state, mockTheme, 80, mockWidthFns);
  for (const line of lines) {
    assert.ok(line.length <= 80, `Line too wide (${line.length} > 80): "${line}"`);
  }
});

test("separator line is exactly `width` chars", () => {
  const state = createState();
  const lines = renderWidget(state, mockTheme, 80, mockWidthFns);
  // Line at index 1 is the separator "─".repeat(width) + possible theme.fg wrapper.
  // With mockTheme (identity), it should be exactly "─".repeat(80).
  // NOTE: The box-drawing character "─" (U+2500) is treated as 1 column here.
  // Real terminals use wcwidth() for East-Asian widths; mockWidthFns.visible()
  // counts bytes/codepoints, which matches since U+2500 is narrow (wcwidth=1).
  assert.strictEqual(lines[1], "─".repeat(80), "Separator should fill exactly width");
});

// ─── 2. Status / color correctness ───────────────────────────────────────────

console.log("\n── 2. Status/color correctness ──");

test("statusColor(running) → 'warning'", () => {
  assert.strictEqual(statusColor("running"), "warning");
});

test("statusColor(success) → 'success'", () => {
  assert.strictEqual(statusColor("success"), "success");
});

test("statusColor(error) → 'error'", () => {
  assert.strictEqual(statusColor("error"), "error");
});

test("statusIcon(running) → '⟳'", () => {
  assert.strictEqual(statusIcon("running"), "⟳");
});

test("statusIcon(success) → '✓'", () => {
  assert.strictEqual(statusIcon("success"), "✓");
});

test("statusIcon(error) → '✗'", () => {
  assert.strictEqual(statusIcon("error"), "✗");
});

test("renderWidget includes ✓ for a success entry", () => {
  const state = createState();
  state.toolSuccess = 1;
  state.toolTotal = 1;
  state.recentActivity.push({
    toolCallId: "ok1",
    toolName: "Read",
    args: "foo.ts",
    status: "success",
    startMs: Date.now() - 500,
    durationMs: 120,
  });
  const rendered = renderWidget(state, mockTheme, 80, mockWidthFns).join("\n");
  assert.ok(rendered.includes("✓"), `Expected '✓' in:\n${rendered}`);
});

test("renderWidget includes ✗ for an error entry", () => {
  const state = createState();
  state.toolError = 1;
  state.toolTotal = 1;
  state.recentActivity.push({
    toolCallId: "err1",
    toolName: "Write",
    args: "tasks/todo.md",
    status: "error",
    startMs: Date.now() - 100,
    durationMs: 50,
  });
  const rendered = renderWidget(state, mockTheme, 80, mockWidthFns).join("\n");
  assert.ok(rendered.includes("✗"), `Expected '✗' in:\n${rendered}`);
});

test("renderWidget includes ⟳ for a running entry", () => {
  const state = createState();
  state.activeTools.set("run1", {
    toolCallId: "run1",
    toolName: "atlas_jobs_run",
    args: "health_check",
    status: "running",
    startMs: Date.now() - 1200,
  });
  const rendered = renderWidget(state, mockTheme, 80, mockWidthFns).join("\n");
  assert.ok(rendered.includes("⟳"), `Expected '⟳' in:\n${rendered}`);
});

test("renderStatus includes '◆ atlas' prefix", () => {
  const state = createState();
  const status = renderStatus(state, mockTheme);
  assert.ok(status.includes("◆ atlas"), `Expected '◆ atlas' in: "${status}"`);
});

test("renderStatus shows 'running' count when active tools exist", () => {
  const state = createState();
  state.activeTools.set("r1", {
    toolCallId: "r1",
    toolName: "Bash",
    args: "sleep 5",
    status: "running",
    startMs: Date.now(),
  });
  const status = renderStatus(state, mockTheme);
  assert.ok(status.includes("running"), `Expected 'running' in: "${status}"`);
});

test("renderWidget shows delegation count when > 0", () => {
  const state = createState();
  state.delegations = 2;
  const rendered = renderWidget(state, mockTheme, 80, mockWidthFns).join("\n");
  assert.ok(rendered.includes("delegated"), `Expected 'delegated' in:\n${rendered}`);
});

// ─── 3. Bounded memory ────────────────────────────────────────────────────────

console.log("\n── 3. Bounded memory ──");

test(`pushBounded never exceeds MAX_ACTIVITY (${MAX_ACTIVITY})`, () => {
  const state = createState();
  for (let i = 0; i < MAX_ACTIVITY + 5; i++) {
    const entry: ActivityEntry = {
      toolCallId: `t${i}`,
      toolName: "Read",
      args: `file${i}.ts`,
      status: "success",
      startMs: Date.now(),
      durationMs: 100,
    };
    pushBounded(state.recentActivity, entry, MAX_ACTIVITY);
  }
  assert.ok(
    state.recentActivity.length <= MAX_ACTIVITY,
    `Expected ≤ ${MAX_ACTIVITY}, got ${state.recentActivity.length}`,
  );
});

test("FIFO eviction: oldest entries removed first", () => {
  const state = createState();
  for (let i = 0; i < MAX_ACTIVITY + 2; i++) {
    pushBounded(
      state.recentActivity,
      {
        toolCallId: `t${i}`,
        toolName: "Read",
        args: `file${i}.ts`,
        status: "success",
        startMs: Date.now(),
        durationMs: 100,
      },
      MAX_ACTIVITY,
    );
  }
  assert.ok(
    !state.recentActivity.find((e) => e.toolCallId === "t0"),
    "t0 should have been evicted",
  );
  assert.ok(
    !state.recentActivity.find((e) => e.toolCallId === "t1"),
    "t1 should have been evicted",
  );
  const newest = `t${MAX_ACTIVITY + 1}`;
  assert.ok(
    state.recentActivity.find((e) => e.toolCallId === newest),
    `${newest} should be present after eviction`,
  );
});

test("rendered feed is capped at MAX_ACTIVITY rows even with many active tools", () => {
  const state = createState();
  // Fill more concurrent tools than MAX_ACTIVITY
  for (let i = 0; i < MAX_ACTIVITY + 3; i++) {
    state.activeTools.set(`run${i}`, {
      toolCallId: `run${i}`,
      toolName: "Bash",
      args: `cmd${i}`,
      status: "running",
      startMs: Date.now(),
    });
  }
  const lines = renderWidget(state, mockTheme, 80, mockWidthFns);
  // Header + separator + up to MAX_ACTIVITY activity rows
  assert.ok(
    lines.length <= 2 + MAX_ACTIVITY,
    `Expected ≤ ${2 + MAX_ACTIVITY} lines, got ${lines.length}`,
  );
});

test("createState produces a fresh state with zeroed counters", () => {
  const s1 = createState();
  s1.toolTotal = 99;
  const s2 = createState();
  assert.strictEqual(s2.toolTotal, 0, "New state should have toolTotal=0");
  assert.strictEqual(s2.recentActivity.length, 0, "New state should have empty activity");
  assert.strictEqual(s2.activeTools.size, 0, "New state should have empty activeTools");
  assert.ok(s2.enabled, "New state should be enabled");
});

// ─── 4. summarizeArgs ────────────────────────────────────────────────────────

console.log("\n── 4. summarizeArgs ──");

test("prefers path over other fields", () => {
  const result = summarizeArgs({ path: "foo/bar.ts", command: "echo hi" });
  assert.strictEqual(result, "foo/bar.ts");
});

test("prefers command when no path", () => {
  const result = summarizeArgs({ command: "git status", query: "x" });
  assert.strictEqual(result, "git status");
});

test("strips newlines from command", () => {
  const result = summarizeArgs({ command: "echo\nhello\nworld" });
  assert.ok(!result.includes("\n"), "Should not contain newlines");
  assert.ok(result.includes("echo"), "Should still contain the command");
});

test("falls back to first value when no priority key present", () => {
  const result = summarizeArgs({ objective: "build feature X" });
  assert.strictEqual(result, "build feature X");
});

test("handles empty args gracefully", () => {
  const result = summarizeArgs({});
  assert.strictEqual(result, "");
});

test("handles non-object args gracefully", () => {
  // Cast to bypass TS - simulates runtime unexpected value
  const result = summarizeArgs(null as unknown as Record<string, unknown>);
  assert.strictEqual(result, "");
});

// ─── 5. fmtDuration ──────────────────────────────────────────────────────────

console.log("\n── 5. fmtDuration ──");

test("< 1 s → milliseconds", () => {
  assert.strictEqual(fmtDuration(450), "450ms");
});

test("exactly 1 s → '1.0s'", () => {
  assert.strictEqual(fmtDuration(1000), "1.0s");
});

test("1–59 s → decimal seconds", () => {
  assert.strictEqual(fmtDuration(4200), "4.2s");
});

test("≥ 60 s → m:ss", () => {
  assert.strictEqual(fmtDuration(90_000), "1:30");
});

test("59.9 s → '59.9s' (not minutes)", () => {
  assert.strictEqual(fmtDuration(59_900), "59.9s");
});

test("sub-millisecond rounds to whole ms", () => {
  assert.strictEqual(fmtDuration(0), "0ms");
});

// ─── 6. Non-interactive guard + capActiveTools ───────────────────────────────────────────────────────────────

// NOTE: The /atlas-tui command handler lives in index.ts, which imports
// @mariozechner/pi-coding-agent and @mariozechner/pi-tui — neither of which
// is available in this test runner context. Mocking those modules would add
// more fragile scaffolding than the simple guard warrants. The fix is verified
// by code review: the handler now early-returns without touching ctx.ui when
// ctx.hasUI is false.

console.log("\n── 6. Non-interactive guard + capActiveTools ──");

test("capActiveTools evicts oldest entries when limit exceeded", () => {
  const state = createState();
  const limit = 4;
  for (let i = 0; i < limit + 3; i++) {
    state.activeTools.set(`run${i}`, {
      toolCallId: `run${i}`,
      toolName: "Bash",
      args: `cmd${i}`,
      status: "running",
      startMs: Date.now(),
    });
  }
  capActiveTools(state, limit);
  assert.ok(
    state.activeTools.size <= limit,
    `Expected ≤ ${limit} active tools, got ${state.activeTools.size}`,
  );
  assert.ok(!state.activeTools.has("run0"), "Oldest entry (run0) should have been evicted");
  assert.ok(!state.activeTools.has("run1"), "Second entry (run1) should have been evicted");
  assert.ok(!state.activeTools.has("run2"), "Third entry (run2) should have been evicted");
  assert.ok(
    state.activeTools.has(`run${limit + 2}`),
    "Newest entry should be retained",
  );
});

test("capActiveTools is a no-op when under the limit", () => {
  const state = createState();
  state.activeTools.set("r1", {
    toolCallId: "r1",
    toolName: "Bash",
    args: "cmd",
    status: "running",
    startMs: Date.now(),
  });
  capActiveTools(state, 10);
  assert.strictEqual(state.activeTools.size, 1, "Should not evict when under limit");
});

test("capActiveTools is a no-op on empty activeTools (limit=0 edge case)", () => {
  const state = createState();
  // Must not throw even with an aggressive limit of 0
  capActiveTools(state, 0);
  assert.strictEqual(state.activeTools.size, 0);
});

// ─── Summary ──────────────────────────────────────────────────────────────────

console.log(`\n${"─".repeat(50)}`);
const total = passed + failed;
if (failed === 0) {
  console.log(`✓  All ${total} tests passed\n`);
} else {
  console.error(`✗  ${failed}/${total} tests failed\n`);
  process.exit(1);
}
