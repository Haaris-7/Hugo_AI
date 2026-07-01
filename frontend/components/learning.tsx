"use client";

import { useQuery } from "@tanstack/react-query";
import { Database, FlaskConical } from "lucide-react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { LearningRun } from "@/lib/types";
import { apiErrorMessage, relativeTime } from "@/lib/utils";
import { EmptyState, ErrorState, LoadingState, PageHeader, StatusBadge } from "@/components/ui";

type Prior = { id: string; niche: string; creator_tier: string; observations: number; mean_cost_per_result: number; win_rate: number; updated_at: string };

export function LearningScreen() {
  const query = useQuery<{ items: LearningRun[]; total: number; strategy_priors: Prior[] }>({ queryKey: ["learning-runs"], queryFn: () => api("/v1/learning-runs"), refetchInterval: 5_000 });
  if (query.isLoading) return <LoadingState label="Loading learning history" />;
  if (query.error || !query.data) return <ErrorState message={apiErrorMessage(query.error)} retry={() => query.refetch()} />;
  const { items, strategy_priors: priors } = query.data;

  return <>
    <PageHeader eyebrow="PostgreSQL baseline" title="Learning" description="Campaign evidence becomes durable priors first. Skill mutation remains optional, isolated, and visible." />
    <div className="rounded-[10px] border border-[#b5dbce] bg-[#edf8f3] p-4 text-sm text-[#245e4c]"><strong>Database learning is the source of truth.</strong> Completion, payments, and prior updates never depend on a skill patch succeeding.</div>

    <section className="mt-6 overflow-hidden rounded-[10px] border border-[#dce4e3] bg-white">
      <div className="flex items-center gap-3 border-b border-[#dce4e3] p-5"><span className="grid h-9 w-9 place-items-center rounded-[8px] bg-[#e6f5f4] text-[#006e6e]"><Database className="h-4 w-4" /></span><div><h2 className="text-[17px] font-semibold">Strategy priors</h2><p className="text-sm text-[#526360]">Evidence supplied to future strategy generation.</p></div></div>
      {priors.length ? <div className="table-scroll"><table className="w-full min-w-[680px] text-left"><caption className="sr-only">Strategy priors learned from completed campaign evidence.</caption><thead className="bg-[#f5f7f7]"><tr className="text-xs font-semibold text-[#526360]"><th className="px-4 py-3">Segment</th><th className="px-4 py-3">Creator tier</th><th className="px-4 py-3">Observations</th><th className="px-4 py-3">Mean cost/result</th><th className="px-4 py-3 text-right">Win rate</th></tr></thead><tbody>{priors.map((prior) => <tr key={prior.id} className="border-t border-[#dce4e3]"><td className="px-4 py-3.5 font-semibold capitalize">{prior.niche}</td><td className="px-4 py-3.5 capitalize">{prior.creator_tier}</td><td className="px-4 py-3.5 tabular-nums">{prior.observations}</td><td className="px-4 py-3.5 tabular-nums">${prior.mean_cost_per_result.toFixed(4)}</td><td className="px-4 py-3.5 text-right font-semibold tabular-nums">{Math.round(prior.win_rate * 100)}%</td></tr>)}</tbody></table></div> : <div className="p-5"><EmptyState title="No priors yet" detail="Complete a campaign to create the first strategy prior." /></div>}
    </section>

    <section className="mt-6 overflow-hidden rounded-[10px] border border-[#dce4e3] bg-white">
      <div className="flex items-center gap-3 border-b border-[#dce4e3] p-5"><FlaskConical className="h-5 w-5 text-[#019393]" /><div><h2 className="text-[17px] font-semibold">Learning runs</h2><p className="text-sm text-[#526360]">Database and experimental patch outcomes are reported separately.</p></div></div>
      {items.length ? <div>{items.map((run) => <Link href={`/campaigns/${run.campaign_id}?tab=learning`} key={run.id} className="grid gap-5 border-b border-[#dce4e3] p-5 last:border-0 hover:bg-[#f7f9f9] md:grid-cols-[1fr_170px_190px_auto] md:items-center"><div><strong>{run.campaign_name}</strong><p className="mt-1 max-w-2xl text-sm text-[#526360]">{run.summary}</p></div><div><p className="mb-2 text-xs font-semibold text-[#687975]">Database baseline</p><StatusBadge value={run.baseline_status} /></div><div><p className="mb-2 flex items-center gap-1 text-xs font-semibold text-[#687975]"><FlaskConical className="h-3 w-3" />Skill patch</p><StatusBadge value={run.patch_status} />{run.patch_status === "no_op" && <p className="mt-1.5 text-xs text-[#687975]">No validated skill change was needed.</p>}</div><time className="text-xs text-[#687975]">{relativeTime(run.updated_at)}</time></Link>)}</div> : <div className="p-5"><EmptyState title="No learning runs" detail="Learning starts automatically after final metrics are recorded." /></div>}
    </section>
  </>;
}
