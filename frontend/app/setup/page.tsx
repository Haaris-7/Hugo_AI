"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { CheckCircle2, CircleAlert, LoaderCircle, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import { apiErrorMessage, titleCase } from "@/lib/utils";

type CapabilityInfo = {
  resolved: "ready" | "missing" | "agent_managed";
  credentials_present: boolean;
  required: boolean;
  managed_by?: string;
};

type SetupSummary = {
  config: Record<string, string>;
  capabilities: Record<string, CapabilityInfo>;
  validation: { ok: boolean; problems: string[] };
  demo_mode?: boolean;
};

type TestResult = {
  capabilities: Record<string, CapabilityInfo & { reachable?: boolean; error?: string }>;
};

type FieldDef = { key: string; label: string; secret?: boolean; optional?: boolean };
const GROUPS: Array<{ title: string; hint: string; fields: FieldDef[] }> = [
  {
    title: "Hermes · NemoClaw",
    hint: "Strategy, negotiation, learning, and agent-managed creator discovery.",
    fields: [
      { key: "ARGO_HERMES_BASE_URL", label: "NemoClaw base URL" },
      { key: "ARGO_HERMES_API_KEY", label: "Hermes API key", secret: true },
    ],
  },
  {
    title: "NVIDIA content QA",
    hint: "Live multimodal verification for draft and final submissions.",
    fields: [
      { key: "ARGO_NVIDIA_API_KEY", label: "NVIDIA API key", secret: true },
      { key: "ARGO_NVIDIA_VISION_MODEL", label: "Vision model" },
    ],
  },
  {
    title: "Stripe",
    hint: "Campaign funding, hosted creator onboarding, and Connect payouts.",
    fields: [
      { key: "ARGO_STRIPE_SECRET_KEY", label: "Stripe secret key", secret: true },
      { key: "ARGO_STRIPE_WEBHOOK_SECRET", label: "Webhook signing secret", secret: true },
    ],
  },
  {
    title: "Creator email",
    hint: "A renewable Gmail OAuth connection lets the worker send deals and process replies continuously.",
    fields: [
      { key: "ARGO_GMAIL_SENDER", label: "Sender address" },
      { key: "ARGO_GMAIL_CLIENT_ID", label: "Google OAuth client ID", secret: true },
      { key: "ARGO_GMAIL_CLIENT_SECRET", label: "Google OAuth client secret", secret: true },
      { key: "ARGO_GMAIL_REFRESH_TOKEN", label: "Google OAuth refresh token", secret: true },
      { key: "ARGO_GMAIL_ACCESS_TOKEN", label: "Temporary access token", secret: true, optional: true },
    ],
  },
  {
    title: "Optional reporting",
    hint: "YouTube metrics and Telegram operator approvals can be connected later.",
    fields: [
      { key: "ARGO_YOUTUBE_API_KEY", label: "YouTube Data API key", secret: true, optional: true },
      { key: "ARGO_TELEGRAM_BOT_TOKEN", label: "Telegram bot token", secret: true, optional: true },
    ],
  },
];

export default function SetupPage() {
  const [summary, setSummary] = useState<SetupSummary | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [demoMode, setDemoMode] = useState(false);
  const [test, setTest] = useState<TestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<SetupSummary>("/v1/system/setup").then((data) => {
      setSummary(data);
      setDemoMode(Boolean(data.demo_mode));
      setValues(Object.fromEntries(
        Object.entries(data.config).filter(([, value]) => value && !value.includes("…") && value !== "set"),
      ));
    }).catch((cause) => setError(apiErrorMessage(cause)));
  }, []);

  async function save() {
    const updates = Object.fromEntries(Object.entries(values).filter(([, value]) => value !== ""));
    updates.ARGO_DEMO_MODE = demoMode ? "true" : "false";
    const result = await api<SetupSummary>("/v1/system/setup", {
      method: "POST",
      body: JSON.stringify({ updates }),
    });
    setSummary(result);
    setDemoMode(Boolean(result.demo_mode));
    setSaved(true);
    return result;
  }

  async function handleSave(testConnections = false) {
    setBusy(true);
    setError(null);
    try {
      await save();
      if (testConnections) {
        setTest(await api<TestResult>("/v1/system/setup/test", { method: "POST", body: "{}" }));
      }
    } catch (cause) {
      setError(apiErrorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-4xl px-5 py-10 sm:px-8 sm:py-14">
      <div className="border-b border-[#dce4e3] pb-7">
        <p className="text-sm font-semibold text-[#006e6e]">Hugo setup</p>
        <h1 className="display-face mt-1 text-[32px] leading-tight">Connect the live runtime</h1>
        <p className="mt-3 max-w-[70ch] text-[15px] leading-6 text-[#526360]">
          Hugo runs only on connected services. Creator discovery is provisioned and managed by Hermes through the influencers.club agent integration, so it has no setup field here.
        </p>
      </div>

      <section className="mt-7 rounded-[10px] border border-[#dce4e3] bg-white p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-[17px] font-semibold">Demo data</h2>
            <p className="mt-1 max-w-[52ch] text-sm leading-5 text-[#526360]">
              Seed demo campaigns — Populates the cockpit with realistic sample data across all lifecycle stages. Disable to remove.
            </p>
          </div>
          <label className="inline-flex min-h-11 cursor-pointer items-center gap-3 rounded-[6px] border border-[#c5d1d0] px-4">
            <span className="text-sm font-medium">{demoMode ? "Enabled" : "Disabled"}</span>
            <input
              type="checkbox"
              className="h-5 w-5 accent-[#019393]"
              checked={demoMode}
              onChange={(event) => {
                setDemoMode(event.target.checked);
                setSaved(false);
              }}
            />
          </label>
        </div>
      </section>

      <div className="mt-7 flex flex-wrap gap-2">
        {summary && Object.entries(summary.capabilities).map(([name, capability]) => (
          <span key={name} className="inline-flex min-h-8 items-center gap-1.5 rounded-[6px] border border-[#dce4e3] bg-white px-2.5 text-xs font-semibold">
            {capability.resolved === "missing" ? <CircleAlert className="h-3.5 w-3.5 text-[#986200]" /> : <CheckCircle2 className="h-3.5 w-3.5 text-[#167a5b]" />}
            {titleCase(name)} · {capability.resolved === "agent_managed" ? "Hermes managed" : capability.resolved}
          </span>
        ))}
      </div>

      {GROUPS.map((group) => (
        <section key={group.title} className="mt-9 border-t border-[#dce4e3] pt-6 first:border-t-0">
          <div className="grid gap-5 md:grid-cols-[240px_1fr]">
            <div><h2 className="text-[17px] font-semibold">{group.title}</h2><p className="mt-1 text-sm leading-5 text-[#526360]">{group.hint}</p></div>
            <div className="grid gap-4">
              {group.fields.map((field) => {
                const configured = Boolean(summary?.config[field.key]);
                return <label key={field.key} className="grid gap-1.5">
                  <span className="flex items-center justify-between text-sm font-medium"><span>{field.label}{field.optional && <span className="ml-1 font-normal text-[#687975]">optional</span>}</span>{configured && <span className="text-xs font-normal text-[#167a5b]">Configured</span>}</span>
                  <input className="h-11 rounded-[6px] border border-[#c5d1d0] bg-white px-3 outline-none focus:border-[#019393]" type={field.secret ? "password" : "text"} value={values[field.key] ?? ""} placeholder={configured && field.secret ? "•••• already stored" : ""} onChange={(event) => { setValues((current) => ({ ...current, [field.key]: event.target.value })); setSaved(false); }} />
                </label>;
              })}
            </div>
          </div>
        </section>
      ))}

      {test && <section className="mt-8 rounded-[10px] border border-[#dce4e3] bg-white p-5"><h2 className="font-semibold">Connection results</h2><div className="mt-3 divide-y divide-[#dce4e3]">{Object.entries(test.capabilities).map(([name, info]) => <div key={name} className="flex min-h-11 items-center justify-between gap-4 py-2 text-sm"><span>{titleCase(name)}</span><span className={info.reachable ? "text-[#167a5b]" : info.resolved === "missing" ? "text-[#986200]" : "text-[#526360]"}>{info.reachable ? "Reachable" : info.error ?? titleCase(info.resolved)}</span></div>)}</div></section>}
      {error && <p role="alert" className="mt-6 text-sm text-[#b42318]">{error}</p>}
      {summary && !summary.validation.ok && <div className="mt-6 rounded-[8px] border border-[#e6c98d] bg-[#fff7e7] p-4"><p className="font-semibold text-[#7d5100]">Configuration incomplete</p><ul className="mt-2 list-disc pl-5 text-sm text-[#654400]">{summary.validation.problems.map((problem) => <li key={problem}>{problem}</li>)}</ul></div>}

      <div className="sticky bottom-4 mt-9 flex flex-wrap items-center justify-between gap-4 rounded-[10px] bg-[#10211f] px-4 py-3 text-white shadow-[0_8px_24px_rgba(16,33,31,.16)]">
        <span className="flex items-center gap-2 text-sm"><ShieldCheck className="h-4 w-4 text-[#76d1cc]" />Secrets stay in the shared server environment.</span>
        <div className="flex gap-2"><button type="button" disabled={busy} onClick={() => handleSave(true)} className="min-h-11 rounded-[6px] border border-white/30 px-4 text-sm font-semibold hover:bg-white/10 disabled:opacity-50">Test connections</button><button type="button" disabled={busy} onClick={() => handleSave(false)} className="inline-flex min-h-11 items-center gap-2 rounded-[6px] bg-[#019393] px-4 text-sm font-semibold text-[#001918] hover:bg-[#27aaa9] disabled:opacity-50">{busy && <LoaderCircle className="h-4 w-4 animate-spin" />}{saved ? "Saved" : "Save configuration"}</button></div>
      </div>
      {saved && summary?.validation.ok && <div className="mt-5 flex items-center justify-between gap-4 rounded-[8px] border border-[#b5dbce] bg-[#edf8f3] p-4"><p className="text-sm text-[#126448]">Live services are configured. The worker will begin autonomous polling.</p><Link href="/" className="font-semibold text-[#006e6e]">Open cockpit →</Link></div>}
    </main>
  );
}
