"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, Check, Database, FlaskConical, LockKeyhole } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { api } from "@/lib/api";
import type { Brand, CampaignSummary } from "@/lib/types";
import { apiErrorMessage, cn } from "@/lib/utils";
import { Button, ErrorState, PageHeader } from "@/components/ui";

const schema = z.object({
  brand_id: z.string(),
  new_brand_name: z.string(),
  niche: z.string().min(2, "Enter a niche"),
  name: z.string().min(2, "Enter a campaign name"),
  goal: z.string().min(5, "Describe the campaign goal"),
  platform: z.enum(["tiktok", "instagram", "youtube"]),
  budget: z.coerce.number().min(1),
  creator_cap: z.coerce.number().min(1),
  operation_mode: z.enum([
    "strategy_creators",
    "strategy_creators_payments",
    "full_autonomy",
  ]),
  hugo_pricing: z.boolean(),
  base_enabled: z.boolean(),
  base_rate: z.coerce.number().min(0),
  cpm_enabled: z.boolean(),
  cpm_rate: z.coerce.number().min(0),
  engagement_enabled: z.boolean(),
  engagement_rate: z.coerce.number().min(0),
  affiliate_enabled: z.boolean(),
  affiliate_rate: z.coerce.number().min(0),
  measurement_window_hours: z.coerce.number().min(1).max(720),
  skill_patch: z.boolean(),
}).refine((data) => data.brand_id || data.new_brand_name.trim(), {
  message: "Choose or create a brand",
  path: ["new_brand_name"],
}).refine((data) => data.creator_cap <= data.budget, {
  message: "Creator cap cannot exceed budget",
  path: ["creator_cap"],
}).refine((data) => data.hugo_pricing || [data.base_enabled, data.cpm_enabled, data.engagement_enabled, data.affiliate_enabled].some(Boolean), {
  message: "Choose at least one component or let Hermes price it",
  path: ["base_enabled"],
});

type FormValues = z.infer<typeof schema>;

const field = "h-11 w-full rounded-[6px] border border-[#c5d1d0] bg-white px-3 outline-none transition-[border-color,box-shadow] focus:border-[#019393] focus:shadow-[0_0_0_3px_rgba(1,147,147,.12)]";

export function CampaignForm() {
  const router = useRouter();
  const brands = useQuery<{ items: Brand[] }>({
    queryKey: ["brands"],
    queryFn: () => api("/v1/brands"),
  });
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      brand_id: "", new_brand_name: "", niche: "fitness", name: "", goal: "",
      platform: "tiktok", budget: 600, creator_cap: 150,
      operation_mode: "full_autonomy", hugo_pricing: true,
      base_enabled: true, base_rate: 150, cpm_enabled: false, cpm_rate: 15,
      engagement_enabled: false, engagement_rate: 25,
      affiliate_enabled: false, affiliate_rate: 10,
      measurement_window_hours: 72, skill_patch: false,
    },
  });
  const hugoPricing = form.watch("hugo_pricing");
  const mutation = useMutation({
    mutationFn: async (values: FormValues) => {
      let brandId = values.brand_id;
      if (!brandId) {
        const brand = await api<{ id: string }>("/v1/brands", {
          method: "POST",
          body: JSON.stringify({ name: values.new_brand_name, niche: values.niche }),
        });
        brandId = brand.id;
      }
      const componentFields = [
        ["base", values.base_enabled, values.base_rate],
        ["cpm", values.cpm_enabled, values.cpm_rate],
        ["engagement", values.engagement_enabled, values.engagement_rate],
        ["affiliate", values.affiliate_enabled, values.affiliate_rate],
      ] as const;
      const components = componentFields
        .filter(([, enabled]) => enabled)
        .map(([kind, , rate]) => ({ kind, rate_cents: Math.round(rate * 100) }));
      return api<CampaignSummary>("/v1/campaigns", {
        method: "POST",
        body: JSON.stringify({
          brand_id: brandId,
          name: values.name,
          goal: values.goal,
          platform: values.platform,
          budget_cents: Math.round(values.budget * 100),
          per_creator_cap_cents: Math.round(values.creator_cap * 100),
          compensation: values.hugo_pricing ? null : { pricing_mode: "user", components },
          operation_mode: values.operation_mode,
          measurement_window_hours: values.measurement_window_hours,
          learning_mode: values.skill_patch ? "database_and_skill_patch" : "database",
        }),
      });
    },
    onSuccess: (campaign) => router.push(`/campaigns/${campaign.id}`),
  });
  const error = (name: keyof FormValues) => form.formState.errors[name]?.message;
  const label = "mb-2 block text-sm font-medium text-[#354542]";

  return <>
    <PageHeader eyebrow="Campaign setup" title="New campaign" description="Lock platform, compensation, QA, and autonomy guardrails before Hermes starts." actions={<Link href="/campaigns"><Button variant="ghost"><ArrowLeft className="h-4 w-4" />Campaigns</Button></Link>} />
    {mutation.error && <div className="mb-6"><ErrorState message={apiErrorMessage(mutation.error)} /></div>}
    <form onSubmit={form.handleSubmit((values) => mutation.mutate(values))} className="grid gap-10 xl:grid-cols-[minmax(0,1fr)_380px]">
      <div className="space-y-9">
        <section>
          <h2 className="text-lg font-semibold">Brand and objective</h2>
          <div className="mt-5 grid gap-5 sm:grid-cols-2">
            <label><span className={label}>Existing brand</span><select {...form.register("brand_id")} className={field}><option value="">Create a new brand</option>{brands.data?.items.map((brand) => <option key={brand.id} value={brand.id}>{brand.name}</option>)}</select></label>
            {!form.watch("brand_id") && <label><span className={label}>New brand name</span><input {...form.register("new_brand_name")} className={field} />{error("new_brand_name") && <small className="mt-1 block text-[#b42318]">{error("new_brand_name")}</small>}</label>}
            <label><span className={label}>Niche</span><input {...form.register("niche")} className={field} /></label>
            <label><span className={label}>Campaign name</span><input {...form.register("name")} className={field} /></label>
            <label><span className={label}>Platform</span><select {...form.register("platform")} className={field}><option value="tiktok">TikTok</option><option value="instagram">Instagram</option><option value="youtube">YouTube</option></select></label>
            <label><span className={label}>Operating mode</span><select {...form.register("operation_mode")} className={field}><option value="full_autonomy">Full autonomy</option><option value="strategy_creators_payments">Approve strategy, creators + payments</option><option value="strategy_creators">Approve strategy + creators</option></select></label>
            <label className="sm:col-span-2"><span className={label}>Goal</span><textarea {...form.register("goal")} className={cn(field, "min-h-24 py-3")} /></label>
          </div>
        </section>

        <section className="border-t border-[#dce4e3] pt-8">
          <h2 className="text-lg font-semibold">Compensation</h2>
          <p className="mt-1 text-sm text-[#526360]">Each selected component becomes an independently QA-gated payout.</p>
          <div className="mt-5 grid gap-5 sm:grid-cols-2">
            <label><span className={label}>Budget (USD)</span><input type="number" {...form.register("budget")} className={field} /></label>
            <label><span className={label}>Per creator cap</span><input type="number" {...form.register("creator_cap")} className={field} />{error("creator_cap") && <small className="mt-1 block text-[#b42318]">{error("creator_cap")}</small>}</label>
          </div>
          <label className="mt-5 flex min-h-14 gap-3 rounded-[8px] border border-[#dce4e3] bg-white p-4"><input type="checkbox" {...form.register("hugo_pricing")} /><span><strong>Let Hermes propose pricing</strong><span className="block text-sm text-[#526360]">The mix and rates appear in strategy preview and lock at approval.</span></span></label>
          {!hugoPricing && <div className="mt-5 grid gap-3 sm:grid-cols-2">
            <ComponentField form={form} enabled="base_enabled" rate="base_rate" title="Base / verified post" />
            <ComponentField form={form} enabled="cpm_enabled" rate="cpm_rate" title="CPM / 1,000 views" />
            <ComponentField form={form} enabled="engagement_enabled" rate="engagement_rate" title="Per 1,000 engagements" />
            <ComponentField form={form} enabled="affiliate_enabled" rate="affiliate_rate" title="Affiliate / conversion" />
            {error("base_enabled") && <small className="text-[#b42318] sm:col-span-2">{error("base_enabled")}</small>}
          </div>}
        </section>

        <section className="border-t border-[#dce4e3] pt-8">
          <h2 className="text-lg font-semibold">Measurement</h2>
          <div className="mt-5 grid gap-5">
            <label><span className={label}>Measurement window (hours)</span><input type="number" {...form.register("measurement_window_hours")} className={field} /></label>
          </div>
        </section>
      </div>

      <aside className="self-start rounded-[10px] border border-[#dce4e3] bg-white p-5 shadow-[0_1px_2px_rgba(16,33,31,.04)] xl:sticky xl:top-8">
        <p className="text-sm font-semibold text-[#354542]">Delivery and learning policy</p>
        <p className="mt-2 text-sm text-[#526360]">Draft media and caption must pass QA before a published URL can be submitted. Final QA gates every payout.</p>
        <div className="mt-5 flex gap-3 rounded-[8px] bg-[#edf8f3] p-4"><Database className="mt-0.5 h-4 w-4 text-[#167a5b]" /><p className="text-sm"><strong>Database learning is always on.</strong><br /><span className="text-[#526360]">Outcomes update creator reputation and strategy priors.</span></p></div>
        <label className={cn("mt-4 flex cursor-pointer gap-3 rounded-[8px] border p-4 transition-colors", form.watch("skill_patch") ? "border-[#019393] bg-[#e6f5f4]" : "border-[#dce4e3] bg-[#f8fafa]")}><input type="checkbox" {...form.register("skill_patch")} /><span className="text-sm"><strong className="flex items-center gap-2"><FlaskConical className="h-4 w-4" />Allow validated skill self-patching</strong><span className="mt-1 block text-[#526360]">Experimental · off by default. Runs only after database learning commits.</span></span></label>
        <p className="mt-4 flex items-center gap-2 text-xs text-[#687975]"><LockKeyhole className="h-3.5 w-3.5" />Learning mode locks when strategy generation begins.</p>
        <Button type="submit" loading={mutation.isPending} className="mt-7 w-full"><Check className="h-4 w-4" />Create campaign</Button>
      </aside>
    </form>
  </>;
}

function ComponentField({ form, enabled, rate, title }: {
  form: ReturnType<typeof useForm<FormValues>>;
  enabled: "base_enabled" | "cpm_enabled" | "engagement_enabled" | "affiliate_enabled";
  rate: "base_rate" | "cpm_rate" | "engagement_rate" | "affiliate_rate";
  title: string;
}) {
  const active = form.watch(enabled);
  return <div className={cn("rounded-[8px] border p-4", active ? "border-[#019393] bg-[#e6f5f4]" : "border-[#dce4e3] bg-white")}>
    <label className="flex items-center gap-2 text-sm font-semibold"><input type="checkbox" {...form.register(enabled)} />{title}</label>
    <div className="mt-3 flex items-center gap-2"><span className="text-sm">$</span><input type="number" step="0.01" {...form.register(rate)} disabled={!active} className={cn(field, "disabled:opacity-40")} /></div>
  </div>;
}
