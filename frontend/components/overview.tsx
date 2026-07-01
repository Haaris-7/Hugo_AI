"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, CheckCircle2, Clock3, Plus, Sparkles } from "lucide-react";
import Link from "next/link";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "@/lib/api";
import type { CampaignSummary, Overview } from "@/lib/types";
import { apiErrorMessage, compactNumber, money, relativeTime, titleCase } from "@/lib/utils";
import { Button, EmptyState, ErrorState, LoadingState, MetricStrip, PageHeader, StatusBadge, TextLink } from "@/components/ui";
import { PlaybookPanel } from "@/components/playbook-panel";

const chartInk = "#526360";
const chartLine = "#dce4e3";

function shortName(name: string) {
  return name.length > 13 ? `${name.slice(0, 12)}…` : name;
}

function campaignResult(campaign: CampaignSummary) {
  return campaign.views || campaign.engagements || campaign.conversions;
}

export function OverviewScreen() {
  const query = useQuery<Overview>({
    queryKey: ["overview"],
    queryFn: () => api("/v1/overview"),
    refetchInterval: 4_000,
  });
  if (query.isLoading) return <LoadingState />;
  if (query.error || !query.data) return <ErrorState message={apiErrorMessage(query.error)} retry={() => query.refetch()} />;

  const data = query.data;
  const measured = data.campaigns.filter(campaignResult);
  const totalViews = data.campaigns.reduce((sum, campaign) => sum + campaign.views, 0);
  const totalEngagements = data.campaigns.reduce((sum, campaign) => sum + campaign.engagements, 0);
  const engagementRate = totalViews ? (totalEngagements / totalViews) * 100 : 0;
  const freeCapital = Math.max(0, data.funded_cents - data.transferred_cents);
  const resultChart = measured.slice(0, 7).reverse().map((campaign) => ({
    name: shortName(campaign.name),
    views: campaign.views,
    engagements: campaign.engagements,
  }));

  return (
    <>
      <PageHeader
        eyebrow="Live operations"
        title="Operator overview"
        description="The decisions, money, and performance that need attention now."
        actions={<Link href="/campaigns/new"><Button><Plus className="h-4 w-4" />New campaign</Button></Link>}
      />

      <MetricStrip items={[
        { label: "Active campaigns", value: String(data.campaigns_active), note: `${data.campaigns_total} total` },
        { label: "Needs your review", value: String(data.pending_actions), note: data.pending_actions ? "Open the action queue" : "Nothing waiting" },
        { label: "Money in", value: money(data.funded_cents), note: `${money(freeCapital)} still held` },
        { label: "Views measured", value: compactNumber(totalViews), note: totalViews ? `${engagementRate.toFixed(1)}% engagement` : "After campaigns finish" },
      ]} />

      <div className="mt-7 grid gap-6 xl:grid-cols-[minmax(330px,.7fr)_minmax(0,1.3fr)]">
        <section className="rounded-[10px] border border-[#dce4e3] bg-[#10211f] p-5 text-white shadow-[0_1px_2px_rgba(16,33,31,.08)]">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-sm font-semibold text-white/70">Operator brief</p>
              <h2 className="mt-1 text-[22px] font-semibold tracking-[-.025em]">{data.pending_actions ? `${data.pending_actions} decisions need review` : "The queue is clear"}</h2>
            </div>
            <span className="grid h-10 w-10 place-items-center rounded-[8px] bg-[#019393] text-[#001918]"><Sparkles className="h-5 w-5" /></span>
          </div>
          <div className="mt-6 border-t border-white/15">
            {data.actions.length ? data.actions.slice(0, 4).map((item) => (
              <Link key={`${item.type}-${item.id}`} href={`/actions?campaign=${item.campaign_id}`} className="group flex min-h-[64px] items-center gap-3 border-b border-white/15 py-3 last:border-b-0">
                <Clock3 className="h-4 w-4 shrink-0 text-[#78cac7]" aria-hidden />
                <span className="min-w-0 flex-1"><strong className="block truncate text-sm">{item.title}</strong><span className="mt-0.5 block truncate text-xs text-white/60">{item.campaign_name} · {money(item.amount_cents)}</span></span>
                <ArrowUpRight className="h-4 w-4 text-white/50 group-hover:text-[#78cac7]" />
              </Link>
            )) : <div className="flex min-h-24 items-center gap-3 text-sm text-white/70"><CheckCircle2 className="h-5 w-5 text-[#78cac7]" />No decisions are waiting.</div>}
          </div>
          <Link href="/actions" className="mt-4 inline-flex min-h-11 items-center text-sm font-semibold text-[#78cac7] hover:text-white">Review action queue <ArrowUpRight className="ml-1.5 h-4 w-4" /></Link>
        </section>

        <section className="rounded-[10px] border border-[#dce4e3] bg-white p-5">
          <div className="flex items-end justify-between gap-4">
            <div><h2 className="text-[17px] font-semibold">Measured performance</h2><p className="mt-1 text-sm text-[#526360]">Views and engagements from campaigns with recorded results.</p></div>
            <span className="text-xs text-[#687975]">{measured.length} measured</span>
          </div>
          {resultChart.length ? (
            <figure className="mt-4 h-64" aria-label="Campaign views and engagements chart">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={resultChart} margin={{ top: 8, right: 10, left: -8, bottom: 0 }}>
                  <CartesianGrid stroke={chartLine} vertical={false} />
                  <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fill: chartInk, fontSize: 11 }} />
                  <YAxis axisLine={false} tickLine={false} tick={{ fill: chartInk, fontSize: 11 }} tickFormatter={compactNumber} />
                  <Tooltip formatter={(value, name) => [Number(value).toLocaleString(), titleCase(String(name))]} cursor={{ fill: "#f5f7f7" }} contentStyle={{ background: "#fff", border: `1px solid ${chartLine}`, borderRadius: 8 }} />
                  <Bar dataKey="views" fill="#019393" radius={[3, 3, 0, 0]} />
                  <Bar dataKey="engagements" fill="#a4b2b0" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
              <figcaption className="sr-only">The measured campaigns generated {totalViews.toLocaleString()} views and {totalEngagements.toLocaleString()} engagements.</figcaption>
            </figure>
          ) : <div className="mt-4"><EmptyState title="No measured results" detail="Performance appears as campaigns close and metrics are recorded." /></div>}
        </section>
      </div>

      <section className="mt-7">
        <div className="mb-4 flex items-end justify-between gap-4"><div><h2 className="text-[17px] font-semibold">Campaigns in motion</h2><p className="mt-1 text-sm text-[#526360]">Current state, budget, learning policy, and last handoff.</p></div><TextLink href="/campaigns">View portfolio</TextLink></div>
        {data.campaigns.length ? (
          <div className="table-scroll overflow-hidden rounded-[10px] border border-[#dce4e3] bg-white">
            <table className="w-full min-w-[760px] border-collapse text-left">
              <caption className="sr-only">Campaigns currently in motion with operating state, budget, learning policy, and last update.</caption>
              <thead className="bg-[#f5f7f7]"><tr className="text-xs font-semibold text-[#526360]"><th className="px-4 py-3">Campaign</th><th className="px-4 py-3">State</th><th className="px-4 py-3">Budget</th><th className="px-4 py-3">Learning</th><th className="px-4 py-3 text-right">Updated</th></tr></thead>
              <tbody>{data.campaigns.map((campaign, index) => (
                <tr key={campaign.id} className="row-enter border-t border-[#dce4e3] hover:bg-[#f7f9f9]" style={{ animationDelay: `${index * 25}ms` }}>
                  <td className="px-4 py-3.5"><Link href={`/campaigns/${campaign.id}`} className="font-semibold hover:text-[#006e6e]">{campaign.name}</Link><p className="mt-0.5 text-xs text-[#687975]">{campaign.brand_name}</p></td>
                  <td className="px-4 py-3.5"><StatusBadge value={campaign.status} /></td>
                  <td className="px-4 py-3.5 font-medium tabular-nums">{money(campaign.budget_cents)}</td>
                  <td className="px-4 py-3.5 text-sm text-[#526360]">{campaign.learning_mode === "database" ? "Database" : "Database + patch"}</td>
                  <td className="px-4 py-3.5 text-right text-xs text-[#687975]">{relativeTime(campaign.updated_at)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : <EmptyState title="No campaigns yet" detail="Create the first campaign to start the operator workflow." />}
      </section>

      <PlaybookPanel />

      <section className="mt-8 rounded-[10px] border border-[#dce4e3] bg-white p-5">
        <div className="flex items-end justify-between gap-4"><div><h2 className="text-[17px] font-semibold">Recent agent activity</h2><p className="mt-1 text-sm text-[#526360]">Authoritative domain events from the runtime.</p></div><span className="text-xs text-[#687975]">Latest {Math.min(data.events.length, 8)}</span></div>
        <ol className="mt-4 divide-y divide-[#dce4e3] border-t border-[#dce4e3]">{data.events.slice(0, 8).map((event) => (
          <li key={`${event.campaign_id}-${event.created_at}-${event.type}`} className="grid grid-cols-[20px_1fr_auto] gap-3 py-3.5"><span className="mt-1.5 h-2 w-2 rounded-full bg-[#019393]" aria-hidden /><span><strong className="block text-sm">{titleCase(event.type.replace(".", " "))}</strong><span className="text-xs text-[#526360]">{event.campaign_name}</span></span><time className="text-xs text-[#687975]">{relativeTime(event.created_at)}</time></li>
        ))}</ol>
      </section>
    </>
  );
}
