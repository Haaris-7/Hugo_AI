"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Plus, Search, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { CampaignSummary } from "@/lib/types";
import { apiErrorMessage, learningModeLabel, money, relativeTime } from "@/lib/utils";
import { Button, EmptyState, ErrorState, LoadingState, PageHeader, StatusBadge } from "@/components/ui";

const control = "h-11 rounded-[6px] border border-[#c5d1d0] bg-white px-3 outline-none transition-[border-color,box-shadow] focus:border-[#019393] focus:shadow-[0_0_0_3px_rgba(1,147,147,.12)]";

export function CampaignList() {
  const params = useSearchParams();
  const router = useRouter();
  const status = params.get("status") ?? "";
  const search = params.get("search") ?? "";
  const query = useQuery<{ items: CampaignSummary[]; total: number }>({
    queryKey: ["campaigns", status, search],
    queryFn: () => api(`/v1/campaigns?status=${encodeURIComponent(status)}&search=${encodeURIComponent(search)}`),
  });
  const update = (key: string, value: string) => {
    const next = new URLSearchParams(params.toString());
    value ? next.set(key, value) : next.delete(key);
    router.replace(`/campaigns?${next.toString()}`);
  };

  return (
    <>
      <PageHeader eyebrow="Portfolio" title="Campaigns" description="Every campaign, operating state, financial envelope, and learning policy." actions={<Link href="/campaigns/new"><Button><Plus className="h-4 w-4" />New campaign</Button></Link>} />
      <div className="mb-5 flex flex-col gap-3 rounded-[10px] border border-[#dce4e3] bg-white p-3 sm:flex-row">
        <label className="relative flex-1">
          <span className="sr-only">Search campaigns</span>
          <Search className="absolute left-3 top-3.5 h-4 w-4 text-[#687975]" />
          <input value={search} onChange={(event) => update("search", event.target.value)} placeholder="Search by campaign name" className={`${control} w-full pl-9`} />
        </label>
        <label className="relative sm:w-56">
          <span className="sr-only">Filter by status</span>
          <SlidersHorizontal className="pointer-events-none absolute left-3 top-3.5 h-4 w-4 text-[#687975]" />
          <select value={status} onChange={(event) => update("status", event.target.value)} className={`${control} w-full pl-9`}>
            <option value="">All states</option><option value="draft">Draft</option><option value="awaiting_approval">Awaiting approval</option><option value="awaiting_funding">Awaiting funding</option><option value="active">Active</option><option value="measuring">Measuring</option><option value="completed">Completed</option>
          </select>
        </label>
      </div>
      {query.isLoading ? <LoadingState label="Loading campaigns" /> : query.error ? <ErrorState message={apiErrorMessage(query.error)} retry={() => query.refetch()} /> : !query.data?.items.length ? <EmptyState title="No campaigns match" detail="Clear the filters or create a new campaign." action={<Link href="/campaigns/new"><Button>New campaign</Button></Link>} /> : (
        <div className="table-scroll overflow-hidden rounded-[10px] border border-[#dce4e3] bg-white">
          <table className="w-full min-w-[900px] text-left">
            <thead className="bg-[#f5f7f7]"><tr className="text-xs font-semibold text-[#526360]"><th className="px-4 py-3">Campaign</th><th className="px-4 py-3">State</th><th className="px-4 py-3">Budget</th><th className="px-4 py-3">Results</th><th className="px-4 py-3">Learning policy</th><th className="px-4 py-3 text-right">Updated</th></tr></thead>
            <tbody>{query.data.items.map((campaign, index) => (
              <tr key={campaign.id} className="row-enter border-t border-[#dce4e3] hover:bg-[#f7f9f9]" style={{ animationDelay: `${index * 25}ms` }}>
                <td className="px-4 py-3.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link href={`/campaigns/${campaign.id}`} className="font-semibold hover:text-[#006e6e]">{campaign.name}</Link>
                    {campaign.is_demo && (
                      <span className="rounded-[4px] bg-[#eef2f2] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[#526360]">
                        Demo
                      </span>
                    )}
                  </div>
                  <p className="mt-1 max-w-sm truncate text-xs text-[#526360]">{campaign.brand_name} · {campaign.goal}</p>
                </td>
                <td className="px-4 py-3.5"><StatusBadge value={campaign.status} /></td>
                <td className="px-4 py-3.5 font-medium tabular-nums">{money(campaign.budget_cents)}</td>
                <td className="px-4 py-3.5 text-sm tabular-nums text-[#354542]">{campaign.views ? `${campaign.views.toLocaleString()} views` : "—"}</td>
                <td className="px-4 py-3.5 text-sm text-[#526360]">{learningModeLabel(campaign.learning_mode)}</td>
                <td className="px-4 py-3.5 text-right text-xs text-[#687975]">{relativeTime(campaign.updated_at)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </>
  );
}
