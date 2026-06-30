"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  CircleDashed,
  ListTodo,
  RefreshCw,
  RotateCcw,
  ServerCog,
  Settings2,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { HermesTask, HermesTaskPreflight } from "@/lib/types";
import { apiErrorMessage, titleCase } from "@/lib/utils";
import { Button, ErrorState, LoadingState, PageHeader, StatusBadge } from "@/components/ui";

type CapabilityInfo = {
  resolved: string;
  credentials_present: boolean;
};

type SystemStatus = {
  environment: string;
  capabilities?: Record<string, CapabilityInfo>;
  services: Array<{ name: string; status: string; detail: string }>;
};

type LiveProbe = {
  hermes: {
    resolved: string;
    ok?: boolean;
    model?: string;
    excerpt?: string;
    latency_ms?: number;
    note?: string;
    error?: string;
  };
  vision: {
    resolved: string;
    model?: string;
    credentials_present?: boolean;
    ok?: boolean;
    latency_ms?: number;
    error?: string;
  };
  stripe: {
    resolved: string;
    credentials_present?: boolean;
    ok?: boolean;
    account_id?: string;
    latency_ms?: number;
    error?: string;
  };
  gmail: {
    resolved: string;
    credentials_present?: boolean;
    ok?: boolean;
    email?: string;
    latency_ms?: number;
    error?: string;
  };
};

const CAPABILITY_LABELS: Record<string, string> = {
  hermes: "Hermes · Nemotron 3 Ultra",
  vision: "NVIDIA NIM vision",
  stripe: "Stripe payments",
  discovery: "Creator discovery",
  gmail: "Outreach email",
};

type AcceptanceProof = {
  campaign_id: string;
  passed: number;
  total: number;
  criteria: Array<{
    id: number;
    name: string;
    passed: boolean;
    evidence?: unknown;
  }>;
};

function formatEvidence(evidence: unknown): string | null {
  if (!evidence) return null;
  if (typeof evidence === "string") return evidence;
  try {
    return JSON.stringify(evidence, null, 2);
  } catch {
    return String(evidence);
  }
}

export function SystemScreen() {
  const query = useQuery<SystemStatus>({
    queryKey: ["system-status"],
    queryFn: () => api("/v1/system/status"),
    refetchInterval: 4_000,
  });
  const proof = useQuery<AcceptanceProof>({
    queryKey: ["acceptance-proof"],
    queryFn: () => api("/v1/system/acceptance-proof"),
    retry: false,
  });
  const liveProbe = useMutation<LiveProbe>({
    mutationFn: () => api("/v1/system/live-probe", { method: "POST", body: "{}" }),
  });
  const taskPreflight = useQuery<HermesTaskPreflight>({
    queryKey: ["hermes-tasks-preflight"],
    queryFn: () => api("/v1/hermes/tasks/preflight"),
    refetchInterval: 4_000,
  });
  const failedTasks = useQuery<{ tasks: HermesTask[]; total: number }>({
    queryKey: ["hermes-tasks-failed"],
    queryFn: () => api("/v1/hermes/tasks?status=failed&limit=20"),
    refetchInterval: 4_000,
  });
  const retryTask = useMutation({
    mutationFn: (taskId: string) =>
      api<HermesTask>(`/v1/hermes/tasks/${taskId}/retry`, { method: "POST", body: "{}" }),
    onSuccess: () => {
      taskPreflight.refetch();
      failedTasks.refetch();
    },
  });
  if (query.isLoading) return <LoadingState label="Checking services" />;
  if (query.error || !query.data) {
    return <ErrorState message={apiErrorMessage(query.error)} retry={() => query.refetch()} />;
  }
  return (
    <>
      <PageHeader
        eyebrow={query.data.environment}
        title="System"
        description="Live runtime dependencies, autonomous worker health, and integration evidence."
        actions={
          <div className="flex gap-2">
            <Link href="/setup">
              <Button variant="secondary">
                <Settings2 className="h-4 w-4" /> Setup wizard
              </Button>
            </Link>
            <Button
              variant="secondary"
              onClick={() => {
                query.refetch();
                proof.refetch();
                taskPreflight.refetch();
                failedTasks.refetch();
              }}
            >
              <RefreshCw className="h-4 w-4" /> Refresh
            </Button>
          </div>
        }
      />

      <section>
        <div className="flex items-center gap-3">
          <ServerCog className="h-5 w-5 text-[#019393]" />
          <h2 className="text-lg font-semibold">Runtime services</h2>
        </div>
        <div className="mt-5 overflow-hidden rounded-[10px] border border-[#dce4e3] bg-white">
          {query.data.services.map((service) => (
            <div
              key={service.name}
              className="grid gap-3 border-b border-[#dce4e3] p-4 last:border-b-0 sm:grid-cols-[1fr_170px_1fr] sm:items-center"
            >
              <strong>{service.name}</strong>
              <StatusBadge value={service.status} />
              <p className="text-sm text-[#526360]">{service.detail}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="mt-12">
        <div className="flex items-end justify-between gap-4">
          <div className="flex items-center gap-3">
            <ListTodo className="h-5 w-5 text-[#019393]" />
            <div>
              <h2 className="text-lg font-semibold">Hermes task queue</h2>
              <p className="text-sm text-[#526360]">
                Durable work items claimed by the Hermes cron loop.
              </p>
            </div>
          </div>
          {taskPreflight.data && (
            <div className="flex gap-3 text-sm tabular-nums">
              <span className="text-[#526360]">{taskPreflight.data.pending} pending</span>
              <span className="text-[#526360]">{taskPreflight.data.claimed} claimed</span>
              <span className={taskPreflight.data.failed ? "font-semibold text-[#b42318]" : "text-[#526360]"}>
                {taskPreflight.data.failed} failed
              </span>
            </div>
          )}
        </div>
        {failedTasks.data?.tasks.length ? (
          <div className="mt-5 overflow-hidden rounded-[10px] border border-[#dce4e3] bg-white">
            {failedTasks.data.tasks.map((task) => (
              <div
                key={task.id}
                className="grid gap-3 border-b border-[#dce4e3] p-4 last:border-b-0 lg:grid-cols-[minmax(0,1fr)_120px_100px] lg:items-center"
              >
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <strong className="text-sm capitalize">{task.task_type.replace("_", " ")}</strong>
                    <StatusBadge value={task.status} />
                    <span className="text-xs text-[#687975]">attempt {task.attempt}</span>
                  </div>
                  <p className="mt-1 text-sm text-[#b42318]">{task.error ?? "Unknown error"}</p>
                  <Link
                    href={`/campaigns/${task.campaign_id}`}
                    className="mt-1 inline-flex text-xs font-semibold text-[#006e6e]"
                  >
                    View campaign
                  </Link>
                </div>
                <time className="text-xs text-[#687975]">{new Date(task.created_at).toLocaleString()}</time>
                <div className="flex justify-end">
                  <Button
                    variant="secondary"
                    loading={retryTask.isPending}
                    onClick={() => retryTask.mutate(task.id)}
                  >
                    <RotateCcw className="h-4 w-4" /> Retry
                  </Button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-5 rounded-[8px] border border-[#dce4e3] bg-white p-5 text-sm text-[#526360]">
            {taskPreflight.data?.failed
              ? "Failed tasks are loading."
              : "No failed tasks. The Hermes cron queue is healthy."}
          </p>
        )}
        {retryTask.error && (
          <p className="mt-3 text-sm text-[#b42318]">{apiErrorMessage(retryTask.error)}</p>
        )}
      </section>

      {query.data.capabilities && (
        <section className="mt-12">
          <div className="flex items-end justify-between gap-4">
            <div className="flex items-center gap-3">
              <Zap className="h-5 w-5 text-[#019393]" />
              <h2 className="text-lg font-semibold">Live primitive modes</h2>
            </div>
            <Button
              variant="secondary"
              loading={liveProbe.isPending}
              onClick={() => liveProbe.mutate()}
            >
              <Zap className="h-4 w-4" /> Run live probe
            </Button>
          </div>
          <div className="mt-5 grid gap-3 sm:grid-cols-2">
            {Object.entries(query.data.capabilities).map(([key, info]) => (
              <div
                key={key}
                className="flex items-center justify-between gap-3 rounded-[8px] border border-[#dce4e3] bg-white px-4 py-3"
              >
                <div>
                  <strong className="text-sm">{CAPABILITY_LABELS[key] ?? key}</strong>
                  <p className="text-xs text-[#687975]">
                    {info.credentials_present ? "credentials present" : "no credentials"}
                  </p>
                </div>
                <span
                  className={`rounded-[6px] px-2.5 py-1 text-xs font-semibold ${
                    ["ready", "agent_managed"].includes(info.resolved)
                      ? "bg-[#edf8f3] text-[#167a5b]"
                      : "bg-[#f1f4f3] text-[#526360]"
                  }`}
                >
                  {info.resolved}
                </span>
              </div>
            ))}
          </div>
          {liveProbe.data && (
            <div className="mt-4 grid gap-3">
              {(["hermes", "vision", "stripe", "gmail"] as const).map((key) => {
                const probe = liveProbe.data[key];
                if (!probe) return null;
                const label = CAPABILITY_LABELS[key] ?? titleCase(key);
                return (
                  <div key={key} className="rounded-[8px] border border-[#dce4e3] bg-white p-4 text-sm">
                    <p className="font-semibold">{label}</p>
                    {probe.ok ? (
                      <p className="mt-1 text-[#167a5b]">
                        Connected
                        {"latency_ms" in probe && probe.latency_ms != null ? ` · ${probe.latency_ms}ms` : ""}
                        {key === "hermes" && "model" in probe && probe.model ? ` · ${probe.model}` : ""}
                        {key === "vision" && "model" in probe && probe.model ? ` · ${probe.model}` : ""}
                        {key === "stripe" && "account_id" in probe && probe.account_id ? ` · ${probe.account_id}` : ""}
                        {key === "gmail" && "email" in probe && probe.email ? ` · ${probe.email}` : ""}
                      </p>
                    ) : (
                      <p className="mt-1 text-[#526360]">
                        {"note" in probe && probe.note
                          ? probe.note
                          : "error" in probe && probe.error
                            ? probe.error
                            : "Not reachable."}
                      </p>
                    )}
                    {key === "hermes" && probe.ok && "excerpt" in probe && probe.excerpt && (
                      <p className="mt-1 text-[#444]">&ldquo;{probe.excerpt}&rdquo;</p>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {liveProbe.error && (
            <p className="mt-3 text-sm text-[#b42318]">{apiErrorMessage(liveProbe.error)}</p>
          )}
        </section>
      )}

      <section className="mt-12">
        <div className="flex items-end justify-between gap-4">
          <div>
            <p className="text-sm font-medium text-[#526360]">
              Acceptance proof
            </p>
            <h2 className="mt-1 text-lg font-semibold">Hackathon integration checklist</h2>
          </div>
          <div className="flex items-center gap-3">
            {proof.data && (
              <strong className="text-2xl tabular-nums">
                {proof.data.passed}/{proof.data.total}
              </strong>
            )}
          </div>
        </div>
        {proof.data ? (
          <ol className="mt-5 overflow-hidden rounded-[10px] border border-[#dce4e3] bg-white">
            {proof.data.criteria.map((criterion) => (
              <li
                key={criterion.id}
                className="border-b border-[#dce4e3] px-4 py-3 last:border-b-0"
              >
                <div className="flex items-center gap-3">
                  {criterion.passed ? (
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-[#167a5b]" />
                  ) : (
                    <CircleDashed className="h-4 w-4 shrink-0 text-[#986200]" />
                  )}
                  <span className="text-xs font-semibold tabular-nums text-[#687975]">
                    {String(criterion.id).padStart(2, "0")}
                  </span>
                  <span className="text-sm">{criterion.name}</span>
                </div>
                {formatEvidence(criterion.evidence) && (
                  <pre className="mt-2 overflow-x-auto rounded-[6px] bg-[#f5f7f7] p-3 text-xs text-[#354542]">
                    {formatEvidence(criterion.evidence)}
                  </pre>
                )}
              </li>
            ))}
          </ol>
        ) : (
          <p className="mt-5 rounded-[8px] border border-[#dce4e3] bg-white p-5 text-sm text-[#526360]">
            Complete a campaign to populate the evidence-backed checklist.
          </p>
        )}
      </section>

      <p className="mt-8 text-xs text-[#687975]">Environment: {titleCase(query.data.environment)} · all evidence is derived from completed live campaign state.</p>
    </>
  );
}
