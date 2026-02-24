import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "@sinclair/typebox";
import { ATLAS_JOB_CATALOG } from "./catalog";
import {
  AtlasJobCancelSchema,
  AtlasJobGetSchema,
  AtlasJobListRunsSchema,
  AtlasJobRunRequestSchema
} from "./schemas";
import type { AtlasJobRunRecord } from "./types";

function nowIso(): string {
  return new Date().toISOString();
}

function makeRunId(job: string): string {
  const safe = job.replace(/[^a-z0-9_]+/gi, "_");
  return `${safe}_${Date.now()}`;
}

function stubRunRecord(
  job: string,
  args?: Record<string, unknown>,
  dryRun?: boolean
): AtlasJobRunRecord {
  const spec = ATLAS_JOB_CATALOG.find((item) => item.name === job);
  return {
    runId: makeRunId(job),
    job: job as AtlasJobRunRecord["job"],
    status: "not_implemented",
    requestedAt: nowIso(),
    finishedAt: nowIso(),
    args,
    dryRun,
    command: spec?.commandPreview,
    artifacts: spec?.artifacts ?? [],
    notImplemented: true,
    error:
      "atlas-jobs execution backend is a skeleton only. Implement process spawning + run state persistence next."
  };
}

export default function atlasJobsExtension(pi: ExtensionAPI) {
  const runStore = new Map<string, AtlasJobRunRecord>();

  pi.registerTool({
    name: "atlas_jobs_list_catalog",
    label: "Atlas Jobs Catalog",
    description:
      "List Atlas-ASX job definitions, expected artifacts, and risk hints used by other Pi workflows.",
    parameters: Type.Object({}),
    async execute() {
      return {
        content: [
          {
            type: "text",
            text: `Atlas jobs catalog (${ATLAS_JOB_CATALOG.length} jobs) loaded.`
          }
        ],
        details: {
          jobs: ATLAS_JOB_CATALOG
        }
      };
    }
  });

  pi.registerTool({
    name: "atlas_jobs_run",
    label: "Atlas Run Job",
    description:
      "Start an Atlas-ASX job by logical name (health check, reoptimize, validate, CLI commands). Skeleton currently validates inputs and returns a stub run record.",
    parameters: AtlasJobRunRequestSchema,
    async execute(_toolCallId, params) {
      const record = stubRunRecord(
        params.job,
        (params.args as Record<string, unknown> | undefined) ?? undefined,
        params.dryRun
      );
      if (params.cwd) {
        record.cwd = params.cwd;
      }
      runStore.set(record.runId, record);
      return {
        content: [
          {
            type: "text",
            text: `Created stub run ${record.runId} for ${record.job}. Execution backend not implemented yet.`
          }
        ],
        details: record
      };
    }
  });

  pi.registerTool({
    name: "atlas_jobs_get",
    label: "Atlas Get Run",
    description:
      "Fetch a previously created Atlas job run record by run ID from the in-memory skeleton store.",
    parameters: AtlasJobGetSchema,
    async execute(_toolCallId, params) {
      const record = runStore.get(params.runId);
      if (!record) {
        return {
          content: [
            {
              type: "text",
              text: `Run ${params.runId} not found in atlas-jobs skeleton store.`
            }
          ],
          details: {
            found: false,
            runId: params.runId
          }
        };
      }

      const details = { ...record };
      if (!params.includeStdoutTail) {
        delete details.stdoutTail;
      }
      if (!params.includeStderrTail) {
        delete details.stderrTail;
      }

      return {
        content: [
          {
            type: "text",
            text: `Run ${params.runId}: ${record.status}`
          }
        ],
        details
      };
    }
  });

  pi.registerTool({
    name: "atlas_jobs_list_runs",
    label: "Atlas List Runs",
    description:
      "List recent Atlas job runs from the skeleton in-memory store, optionally filtered by job or status.",
    parameters: AtlasJobListRunsSchema,
    async execute(_toolCallId, params) {
      const limit = params.limit ?? 20;
      let runs = Array.from(runStore.values()).sort((a, b) =>
        b.requestedAt.localeCompare(a.requestedAt)
      );
      if (params.job) {
        runs = runs.filter((run) => run.job === params.job);
      }
      if (params.status) {
        runs = runs.filter((run) => run.status === params.status);
      }
      runs = runs.slice(0, limit);

      return {
        content: [
          {
            type: "text",
            text: `Returned ${runs.length} Atlas run record(s) from skeleton store.`
          }
        ],
        details: {
          count: runs.length,
          runs
        }
      };
    }
  });

  pi.registerTool({
    name: "atlas_jobs_cancel",
    label: "Atlas Cancel Run",
    description:
      "Cancel a queued/running Atlas job. Skeleton marks a stored run as canceled; no process management yet.",
    parameters: AtlasJobCancelSchema,
    async execute(_toolCallId, params) {
      const record = runStore.get(params.runId);
      if (!record) {
        return {
          content: [
            {
              type: "text",
              text: `Run ${params.runId} not found.`
            }
          ],
          details: {
            canceled: false,
            runId: params.runId
          }
        };
      }

      record.status = "canceled";
      record.finishedAt = nowIso();
      if (params.reason) {
        record.error = `Canceled: ${params.reason}`;
      }
      runStore.set(record.runId, record);

      return {
        content: [
          {
            type: "text",
            text: `Run ${params.runId} marked canceled in skeleton store.`
          }
        ],
        details: {
          canceled: true,
          record
        }
      };
    }
  });
}
