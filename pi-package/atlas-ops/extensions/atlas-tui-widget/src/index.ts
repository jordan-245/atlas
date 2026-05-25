/**
 * Atlas TUI Widget — Pi Extension Entry Point
 *
 * Renders a compact live-activity dashboard above the Pi editor.
 * Dashboard Pro style: dense, data-first, status-color coded.
 *
 * Display:
 *   ◆ ATLAS  │  tools 17  ✓ 15  │  ✗ 2  │  ⏱ 02:34
 *   ──────────────────────────────────────────────────
 *     ✓ Read              memory/SUMMARY.md     200ms
 *     ⟳ atlas_jobs_run    health_check          1.2s…
 *
 * Commands:
 *   /atlas-tui         — Toggle widget on/off
 *   /atlas-tui reset   — Clear stats, restart session timer
 *
 * Width safety: every rendered line is passed through truncateToWidth().
 * Non-interactive mode: all UI calls are gated on ctx.hasUI.
 *
 * Pure core logic (state, render) lives in ./core.ts for testability
 * without a Pi runtime dependency.
 */

import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { truncateToWidth, visibleWidth } from "@mariozechner/pi-tui";

import {
  WIDGET_ID,
  capActiveTools,
  createState,
  pushBounded,
  renderStatus,
  renderWidget,
  summarizeArgs,
  type ActivityEntry,
  type Theme,
  type TuiState,
  type WidthFns,
  MAX_ACTIVITY,
} from "./core";

// Pi-TUI width functions wired once.
const piWidthFns: WidthFns = {
  truncate: truncateToWidth,
  visible: visibleWidth,
};

const STATUS_ID = WIDGET_ID;

// ─── Extension entry point ────────────────────────────────────────────────────

export default function atlasTuiWidget(pi: ExtensionAPI) {
  let state: TuiState = createState();
  /**
   * Captured from the widget factory so event handlers can request re-renders
   * without rebuilding the factory closure via a second setWidget() call.
   */
  let requestRender: (() => void) | undefined;

  // ── Widget mount / unmount ──────────────────────────────────────────────────

  type UiCtx = {
    ui: {
      setWidget: (
        id: string,
        factory:
          | ((
              tui: { requestRender(): void },
              theme: Theme,
            ) => { render(w: number): string[]; invalidate(): void })
          | undefined,
        opts?: { placement?: string },
      ) => void;
      setStatus: (id: string, text: string | undefined) => void;
      theme: Theme;
    };
  };

  function mountWidget(ctx: UiCtx) {
    if (!state.enabled) return;

    ctx.ui.setWidget(
      WIDGET_ID,
      (tui, theme) => {
        requestRender = () => tui.requestRender();
        return {
          render: (width: number) =>
            renderWidget(state, theme, width, piWidthFns),
          invalidate: () => {},
        };
      },
      { placement: "aboveEditor" },
    );

    ctx.ui.setStatus(STATUS_ID, renderStatus(state, ctx.ui.theme));
  }

  function unmountWidget(ctx: {
    ui: { setWidget(id: string, f: undefined): void; setStatus(id: string, t: undefined): void };
  }) {
    ctx.ui.setWidget(WIDGET_ID, undefined);
    ctx.ui.setStatus(STATUS_ID, undefined);
    requestRender = undefined;
  }

  function refreshStatus(ctx: {
    ui: { setStatus(id: string, text: string): void; theme: Theme };
  }) {
    if (!state.enabled) return;
    ctx.ui.setStatus(STATUS_ID, renderStatus(state, ctx.ui.theme));
    requestRender?.();
  }

  // ── Session lifecycle ───────────────────────────────────────────────────────

  pi.on("session_start", async (_event, ctx) => {
    if (!ctx.hasUI) return;
    state = createState();
    mountWidget(ctx);
  });

  pi.on("session_shutdown", async (_event, ctx) => {
    if (!ctx.hasUI) return;
    unmountWidget(ctx);
  });

  // ── Agent lifecycle ─────────────────────────────────────────────────────────

  pi.on("agent_start", async (_event, ctx) => {
    if (!ctx.hasUI || !state.enabled) return;
    refreshStatus(ctx);
  });

  pi.on("agent_end", async (_event, ctx) => {
    if (!ctx.hasUI || !state.enabled) return;
    refreshStatus(ctx);
  });

  // ── Tool lifecycle ──────────────────────────────────────────────────────────

  pi.on("tool_execution_start", async (event, ctx) => {
    if (!ctx.hasUI || !state.enabled) return;

    const entry: ActivityEntry = {
      toolCallId: event.toolCallId,
      toolName: event.toolName,
      args: summarizeArgs((event.args as Record<string, unknown>) ?? {}),
      status: "running",
      startMs: Date.now(),
    };

    state.activeTools.set(event.toolCallId, entry);
    state.toolTotal++;

    // Defensive cap: prevent unbounded growth if tool_execution_end is missed.
    capActiveTools(state);

    // Delegation tools (subagent, swarm, delegate*)
    if (
      event.toolName === "subagent" ||
      event.toolName === "swarm" ||
      event.toolName.startsWith("delegate")
    ) {
      state.delegations++;
    }

    refreshStatus(ctx);
  });

  pi.on("tool_execution_end", async (event, ctx) => {
    if (!ctx.hasUI || !state.enabled) return;

    const entry = state.activeTools.get(event.toolCallId);
    if (entry) {
      entry.status = event.isError ? "error" : "success";
      entry.durationMs = Date.now() - entry.startMs;
      state.activeTools.delete(event.toolCallId);

      if (event.isError) {
        state.toolError++;
      } else {
        state.toolSuccess++;
      }

      pushBounded(state.recentActivity, entry, MAX_ACTIVITY);
    }

    refreshStatus(ctx);
  });

  // ── Toggle command ──────────────────────────────────────────────────────────

  pi.registerCommand("atlas-tui", {
    description: "Toggle Atlas TUI dashboard widget. Sub-commands: reset",
    handler: async (args, ctx) => {
      if (!ctx.hasUI) {
        return; // silently no-op — ctx.ui is unavailable in non-interactive mode
      }

      const sub = args.trim().toLowerCase();

      if (sub === "reset") {
        state = createState();
        mountWidget(ctx);
        ctx.ui.notify("Atlas TUI reset", "info");
        return;
      }

      state.enabled = !state.enabled;

      if (state.enabled) {
        mountWidget(ctx);
        ctx.ui.notify("Atlas TUI enabled", "info");
      } else {
        unmountWidget(ctx);
        ctx.ui.notify("Atlas TUI hidden — /atlas-tui to restore", "info");
      }
    },
  });
}
