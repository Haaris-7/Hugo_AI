"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ExternalLink, ShieldAlert, X } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import type { ActionItem } from "@/lib/types";
import { apiErrorMessage, money, relativeTime, titleCase } from "@/lib/utils";
import { Button, EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/ui";

const filters = ["", "strategy", "deal", "service_spend", "payout"];

export function ActionQueue() {
  const params = useSearchParams();
  const router = useRouter();
  const client = useQueryClient();
  const type = params.get("type") ?? "";
  const query = useQuery<{ items: ActionItem[]; total: number }>({ queryKey: ["action-queue", type], queryFn: () => api(`/v1/action-queue?type=${type}`), refetchInterval: 4_000 });
  const mutation = useMutation({
    mutationFn: ({ item, decision }: { item: ActionItem; decision: "approved" | "rejected" }) => item.type === "payout" ? api(`/v1/payouts/${item.id}/release`, { method: "POST", body: "{}" }) : api("/v1/approvals", { method: "POST", body: JSON.stringify({ campaign_id: item.campaign_id, resource_type: item.type, resource_id: item.id, decision, expected_version: item.expected_version }) }),
    onSuccess: () => { client.invalidateQueries({ queryKey: ["action-queue"] }); client.invalidateQueries({ queryKey: ["overview"] }); },
  });
  const setType = (value: string) => { const next = new URLSearchParams(params.toString()); value ? next.set("type", value) : next.delete("type"); router.replace(`/actions?${next.toString()}`); };

  return <>
    <PageHeader eyebrow="Human control" title="Action queue" description="Review evidence and financial impact before money, outreach, or policy-sensitive work proceeds." />
    <div className="mb-5 flex gap-1 overflow-x-auto rounded-[8px] border border-[#dce4e3] bg-white p-1" role="tablist" aria-label="Decision type">
      {filters.map((value) => <button key={value} role="tab" aria-selected={type === value} onClick={() => setType(value)} className={`min-h-10 whitespace-nowrap rounded-[6px] px-3 text-sm font-medium transition-colors ${type === value ? "bg-[#e6f5f4] text-[#006e6e]" : "text-[#526360] hover:bg-[#eef2f2] hover:text-[#10211f]"}`}>{value ? titleCase(value) : "All decisions"}</button>)}
    </div>
    {mutation.error && <div className="mb-5"><ErrorState message={apiErrorMessage(mutation.error)} /></div>}
    {query.isLoading ? <LoadingState label="Loading action queue" /> : query.error ? <ErrorState message={apiErrorMessage(query.error)} retry={() => query.refetch()} /> : !query.data?.items.length ? <EmptyState title="Queue clear" detail="Decisions appear here when Hermes needs approval for strategy, outreach, spending, or payouts." /> : (
      <div className="overflow-hidden rounded-[10px] border border-[#dce4e3] bg-white">
        {query.data.items.map((item, index) => <article key={`${item.type}-${item.id}`} className="row-enter grid gap-4 border-b border-[#dce4e3] p-5 last:border-0 lg:grid-cols-[140px_minmax(0,1fr)_130px_220px] lg:items-center" style={{ animationDelay: `${index * 25}ms` }}>
          <div><span className="inline-flex items-center gap-2 text-xs font-semibold text-[#526360]"><ShieldAlert className="h-3.5 w-3.5 text-[#986200]" />{titleCase(item.type)}</span><p className="mt-2 text-xs text-[#687975]">{relativeTime(item.created_at)} · v{item.expected_version}</p></div>
          <div><h2 className="font-semibold">{item.title}</h2><p className="mt-1 max-w-2xl text-sm text-[#526360]">{item.detail}</p><Link href={`/campaigns/${item.campaign_id}`} className="mt-2 inline-flex min-h-8 items-center gap-1 text-xs font-semibold text-[#006e6e]">{item.campaign_name}<ExternalLink className="h-3 w-3" /></Link></div>
          <div><p className="text-xs text-[#687975]">Financial impact</p><p className="mt-1 font-semibold tabular-nums">{money(item.amount_cents)}</p></div>
          <div className="flex justify-end gap-2">{item.type !== "payout" && <Button variant="ghost" disabled={mutation.isPending} onClick={() => mutation.mutate({ item, decision: "rejected" })}><X className="h-4 w-4" />Reject</Button>}<Button loading={mutation.isPending} onClick={() => mutation.mutate({ item, decision: "approved" })}><Check className="h-4 w-4" />{item.type === "payout" ? "Release" : "Approve"}</Button></div>
        </article>)}
      </div>
    )}
  </>;
}
