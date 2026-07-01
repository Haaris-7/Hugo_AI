"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowLeft, CheckCircle2, CircleAlert, ExternalLink, LoaderCircle, ShieldCheck } from "lucide-react";
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

type FieldDef = {
  key: string;
  label: string;
  secret?: boolean;
  optional?: boolean;
  options?: Array<{ value: string; label: string }>;
  emailMode?: "gmail_api" | "browser";
};
type HelpLink = { label: string; href: string };
const GROUPS: Array<{ title: string; hint: string; fields: FieldDef[]; links: HelpLink[] }> = [
  {
    title: "Hermes · NemoClaw",
    hint: "Strategy, learning, fixed-offer outreach, and agent-managed creator discovery.",
    fields: [
      { key: "HUGO_HERMES_BASE_URL", label: "NemoClaw base URL" },
      { key: "HUGO_HERMES_API_KEY", label: "Hermes API key", secret: true },
    ],
    links: [
      { label: "NemoClaw Hermes setup", href: "https://docs.nvidia.com/nemoclaw/latest/get-started/quickstart-hermes.html" },
    ],
  },
  {
    title: "NVIDIA content QA",
    hint: "Live multimodal verification for draft and final submissions.",
    fields: [
      { key: "HUGO_NVIDIA_API_KEY", label: "NVIDIA API key", secret: true },
      { key: "HUGO_NVIDIA_VISION_MODEL", label: "Vision model" },
    ],
    links: [
      { label: "Create an NVIDIA API key", href: "https://build.nvidia.com/settings/api-keys" },
    ],
  },
  {
    title: "Stripe",
    hint: "Campaign funding, hosted creator onboarding, and Connect payouts.",
    fields: [
      { key: "HUGO_STRIPE_SECRET_KEY", label: "Stripe secret key", secret: true },
      { key: "HUGO_STRIPE_WEBHOOK_SECRET", label: "Webhook signing secret", secret: true },
    ],
    links: [
      { label: "Stripe API keys", href: "https://docs.stripe.com/keys" },
      { label: "Webhook signing secrets", href: "https://docs.stripe.com/webhooks/signature" },
    ],
  },
  {
    title: "Creator email",
    hint: "Choose unattended Gmail API delivery or let Hermes compose and send through a connected, signed-in browser.",
    fields: [
      {
        key: "HUGO_EMAIL_TRANSPORT",
        label: "Email delivery mode",
        options: [
          { value: "gmail_api", label: "Gmail API — unattended" },
          { value: "browser", label: "Browser automation — Gmail or Outlook" },
        ],
      },
      {
        key: "HUGO_BROWSER_EMAIL_PROVIDER",
        label: "Email service",
        options: [
          { value: "gmail", label: "Gmail" },
          { value: "outlook", label: "Outlook" },
        ],
        emailMode: "browser",
      },
      { key: "HUGO_BROWSER_EMAIL_SENDER", label: "Sender email address", emailMode: "browser" },
      { key: "HUGO_GMAIL_SENDER", label: "Sender address", emailMode: "gmail_api" },
      { key: "HUGO_GMAIL_CLIENT_ID", label: "Google OAuth client ID", secret: true, emailMode: "gmail_api" },
      { key: "HUGO_GMAIL_CLIENT_SECRET", label: "Google OAuth client secret", secret: true, emailMode: "gmail_api" },
      { key: "HUGO_GMAIL_REFRESH_TOKEN", label: "Google OAuth refresh token", secret: true, emailMode: "gmail_api" },
      { key: "HUGO_GMAIL_ACCESS_TOKEN", label: "Temporary access token", secret: true, optional: true, emailMode: "gmail_api" },
    ],
    links: [
      { label: "Create Google OAuth credentials", href: "https://console.cloud.google.com/apis/credentials" },
      { label: "Configure Gmail server authorization", href: "https://developers.google.com/workspace/gmail/api/auth/web-server" },
      { label: "Connect Hermes to your browser", href: "https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/browser.md" },
    ],
  },
  {
    title: "Creator discovery",
    hint: "Choose how Hugo finds creators. Influencers.club provides reliable verified emails. Hermes agents use web research but may struggle to find email addresses.",
    fields: [
      { key: "HUGO_DISCOVERY_MODE", label: "Discovery method" },
      { key: "HUGO_INFLUENCERS_CLUB_API_KEY", label: "Influencers.club API key", secret: true, optional: true },
    ],
    links: [
      { label: "Influencers.club API access", href: "https://influencers.club" },
    ],
  },
  {
    title: "Optional reporting",
    hint: "YouTube metrics and Telegram operator approvals can be connected later. Create Telegram bots through the official @BotFather account.",
    fields: [
      { key: "HUGO_YOUTUBE_API_KEY", label: "YouTube Data API key", secret: true, optional: true },
      { key: "HUGO_TELEGRAM_BOT_TOKEN", label: "Telegram bot token", secret: true, optional: true },
    ],
    links: [
      { label: "YouTube API credentials", href: "https://developers.google.com/youtube/registering_an_application" },
      { label: "Create a Telegram bot token", href: "https://core.telegram.org/bots/tutorial#obtain-your-bot-token" },
    ],
  },
];

const DEMO_REAL_OPTIONS: Array<{ capability: string; label: string }> = [
  { capability: "hermes", label: "Hermes / NemoClaw (strategy and learning)" },
  { capability: "vision", label: "NVIDIA Vision (content QA)" },
  { capability: "stripe", label: "Stripe (funding, payouts)" },
  { capability: "gmail", label: "Gmail (creator outreach)" },
];

export default function SetupPage() {
  const [summary, setSummary] = useState<SetupSummary | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [demoMode, setDemoMode] = useState(false);
  const [demoReal, setDemoReal] = useState<Set<string>>(new Set());
  const [test, setTest] = useState<TestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<SetupSummary>("/v1/system/setup").then((data) => {
      setSummary(data);
      setDemoMode(Boolean(data.demo_mode));
      const raw = data.config["HUGO_DEMO_REAL_PROVIDERS"] ?? "";
      setDemoReal(new Set(raw.split(",").map((s: string) => s.trim()).filter(Boolean)));
      const configured = Object.fromEntries(
        Object.entries(data.config).filter(([, value]) => value && !value.includes("…") && value !== "set"),
      );
      setValues({
        HUGO_EMAIL_TRANSPORT: "gmail_api",
        HUGO_BROWSER_EMAIL_PROVIDER: "gmail",
        ...configured,
      });
    }).catch((cause) => setError(apiErrorMessage(cause)));
  }, []);

  async function save() {
    const updates = Object.fromEntries(Object.entries(values).filter(([, value]) => value !== ""));
    updates.HUGO_DEMO_MODE = demoMode ? "true" : "false";
    updates.HUGO_DEMO_REAL_PROVIDERS = [...demoReal].join(",");
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
        <div className="mb-4 flex items-center justify-between gap-4">
          <p className="text-sm font-semibold text-[#006e6e]">Hugo setup</p>
          <Link href="/system" className="inline-flex min-h-11 items-center gap-2 rounded-[6px] px-3 text-sm font-semibold text-[#354542] hover:bg-[#eef2f2] hover:text-[#10211f]">
            <ArrowLeft className="h-4 w-4" aria-hidden /> Back to system
          </Link>
        </div>
        <h1 className="display-face mt-1 text-[32px] leading-tight">Connect the live runtime</h1>
        <p className="mt-3 max-w-[70ch] text-[15px] leading-6 text-[#526360]">
          Hugo runs only on connected services. Configure each integration below, then choose how creator discovery works — via influencers.club API or Hermes agent web research.
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
        {demoMode && <>
          {(() => {
            const configured = DEMO_REAL_OPTIONS.filter(
              (opt) => summary?.capabilities[opt.capability]?.credentials_present,
            );
            return configured.length > 0 ? (
              <div className="mt-5 border-t border-[#dce4e3] pt-4">
                <p className="text-sm font-medium">Use real services in demo</p>
                <p className="mt-1 max-w-[56ch] text-xs leading-4 text-[#526360]">
                  You have API keys configured for these services. Check the ones you want to use live instead of simulated responses.
                </p>
                <div className="mt-3 grid gap-2">
                  {configured.map((opt) => (
                    <label key={opt.capability} className="inline-flex cursor-pointer items-center gap-3 text-sm">
                      <input
                        type="checkbox"
                        className="h-4 w-4 accent-[#019393]"
                        checked={demoReal.has(opt.capability)}
                        onChange={(event) => {
                          setDemoReal((prev) => {
                            const next = new Set(prev);
                            if (event.target.checked) next.add(opt.capability);
                            else next.delete(opt.capability);
                            return next;
                          });
                          setSaved(false);
                        }}
                      />
                      <span>{opt.label}</span>
                    </label>
                  ))}
                </div>
              </div>
            ) : null;
          })()}
          <div className="mt-5 border-t border-[#dce4e3] pt-4">
            <p className="text-sm font-medium">NVIDIA Build — AI-generated content</p>
            <p className="mt-1 max-w-[56ch] text-xs leading-4 text-[#526360]">
              Provide an NVIDIA Build API key to generate real AI strategy, outreach, and learning content instead of canned demo responses.
            </p>
            <div className="mt-3 grid gap-3">
              <label className="grid gap-1.5">
                <span className="text-sm font-medium">NVIDIA Build API key <span className="font-normal text-[#687975]">optional</span></span>
                <input className="h-11 rounded-[6px] border border-[#c5d1d0] bg-white px-3 outline-none focus:border-[#019393]" type="password" value={values.HUGO_NVIDIA_BUILD_API_KEY ?? ""} placeholder={summary?.config.HUGO_NVIDIA_BUILD_API_KEY ? "•••• already stored" : "nvapi-…"} onChange={(event) => { setValues((current) => ({ ...current, HUGO_NVIDIA_BUILD_API_KEY: event.target.value })); setSaved(false); }} />
              </label>
              <label className="grid gap-1.5">
                <span className="text-sm font-medium">Model</span>
                <input className="h-11 rounded-[6px] border border-[#c5d1d0] bg-white px-3 outline-none focus:border-[#019393]" type="text" value={values.HUGO_NVIDIA_BUILD_MODEL ?? "nvidia/llama-3.3-nemotron-super-49b-v1"} onChange={(event) => { setValues((current) => ({ ...current, HUGO_NVIDIA_BUILD_MODEL: event.target.value })); setSaved(false); }} />
              </label>
            </div>
            <a href="https://build.nvidia.com/settings/api-keys" target="_blank" rel="noreferrer" className="mt-2 inline-flex min-h-8 items-center gap-1.5 text-xs font-semibold text-[#006e6e] hover:text-[#004e4e] hover:underline">
              Get an NVIDIA Build API key<ExternalLink className="h-3 w-3" aria-hidden />
            </a>
          </div>
        </>}
      </section>

      <div className="mt-7 flex flex-wrap gap-2">
        {summary && Object.entries(summary.capabilities).map(([name, capability]) => (
          <span key={name} className="inline-flex min-h-8 items-center gap-1.5 rounded-[6px] border border-[#dce4e3] bg-white px-2.5 text-xs font-semibold">
            {capability.resolved === "missing" ? <CircleAlert className="h-3.5 w-3.5 text-[#986200]" /> : <CheckCircle2 className="h-3.5 w-3.5 text-[#167a5b]" />}
            {titleCase(name)} · {capability.resolved === "agent_managed" ? "Hermes agents" : capability.resolved}
          </span>
        ))}
      </div>

      <p className="mt-6 text-sm text-[#526360]">Fields marked “Required for live use” may be left empty while demo mode is enabled.</p>

      {GROUPS.map((group) => (
        <section key={group.title} className="mt-9 border-t border-[#dce4e3] pt-6 first:border-t-0">
          <div className="grid gap-5 md:grid-cols-[240px_1fr]">
            <div>
              <h2 className="text-[17px] font-semibold">{group.title}</h2>
              <p className="mt-1 text-sm leading-5 text-[#526360]">{group.hint}</p>
              <div className="mt-3 flex flex-col items-start gap-1">
                {group.links.map((link) => (
                  <a key={link.href} href={link.href} target="_blank" rel="noreferrer" className="inline-flex min-h-8 items-center gap-1.5 text-xs font-semibold text-[#006e6e] hover:text-[#004e4e] hover:underline">
                    {link.label}<ExternalLink className="h-3 w-3" aria-hidden />
                  </a>
                ))}
              </div>
            </div>
            <div className="grid gap-4">
              {group.fields.map((field) => {
                const configured = Boolean(summary?.config[field.key]);
                const emailTransport = values.HUGO_EMAIL_TRANSPORT || "gmail_api";
                if (field.emailMode && field.emailMode !== emailTransport) {
                  return null;
                }
                if (field.options) {
                  return <label key={field.key} className="grid gap-1.5">
                    <span className="flex items-center justify-between gap-3 text-sm font-medium"><span>{field.label}</span><span className="text-xs font-normal text-[#687975]">Required for live use</span></span>
                    <select className="h-11 rounded-[6px] border border-[#c5d1d0] bg-white px-3 outline-none focus:border-[#019393]" value={values[field.key] ?? field.options[0].value} onChange={(event) => { setValues((current) => ({ ...current, [field.key]: event.target.value })); setSaved(false); }}>
                      {field.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                  </label>;
                }
                if (field.key === "HUGO_DISCOVERY_MODE") {
                  return <label key={field.key} className="grid gap-1.5">
                    <span className="flex items-center justify-between gap-3 text-sm font-medium"><span>{field.label}</span><span className="text-xs font-normal text-[#687975]">Required for live use</span></span>
                    <select className="h-11 rounded-[6px] border border-[#c5d1d0] bg-white px-3 outline-none focus:border-[#019393]" value={values[field.key] ?? "hermes_agents"} onChange={(event) => { setValues((current) => ({ ...current, [field.key]: event.target.value })); setSaved(false); }}>
                      <option value="hermes_agents">Hermes agents (web research)</option>
                      <option value="influencers_club">Influencers.club API</option>
                    </select>
                  </label>;
                }
                if (field.key === "HUGO_INFLUENCERS_CLUB_API_KEY" && (values["HUGO_DISCOVERY_MODE"] ?? "hermes_agents") !== "influencers_club") {
                  return null;
                }
                return <label key={field.key} className="grid gap-1.5">
                  <span className="flex items-center justify-between gap-3 text-sm font-medium"><span>{field.label}{field.optional && <span className="ml-1 font-normal text-[#687975]">optional</span>}</span>{configured ? <span className="text-xs font-normal text-[#167a5b]">Configured</span> : !field.optional ? <span className="text-xs font-normal text-[#687975]">Required for live use</span> : null}</span>
                  <input className="h-11 rounded-[6px] border border-[#c5d1d0] bg-white px-3 outline-none focus:border-[#019393]" type={field.secret ? "password" : field.key.includes("SENDER") ? "email" : "text"} value={values[field.key] ?? ""} placeholder={configured && field.secret ? "•••• already stored" : ""} onChange={(event) => { setValues((current) => ({ ...current, [field.key]: event.target.value })); setSaved(false); }} />
                  {field.key === "HUGO_BROWSER_EMAIL_SENDER" && <div className="flex flex-wrap items-center justify-between gap-2 text-xs leading-5 text-[#526360]"><p>Hermes verifies this exact account before sending. One time: run <code>hermes tools</code>, enable Browser, then run <code>/browser connect</code> for the signed-in Chrome or Edge session.</p><a href={(values.HUGO_BROWSER_EMAIL_PROVIDER || "gmail") === "outlook" ? "https://outlook.office.com/mail/" : "https://mail.google.com/"} target="_blank" rel="noreferrer" className="inline-flex min-h-8 items-center gap-1 font-semibold text-[#006e6e] hover:underline">Open {(values.HUGO_BROWSER_EMAIL_PROVIDER || "gmail") === "outlook" ? "Outlook" : "Gmail"}<ExternalLink className="h-3 w-3" aria-hidden /></a></div>}
                </label>;
              })}
            </div>
          </div>
        </section>
      ))}

      {test && <section aria-live="polite" className="mt-8 rounded-[10px] border border-[#dce4e3] bg-white p-5"><h2 className="font-semibold">Connection results</h2><div className="mt-3 divide-y divide-[#dce4e3]">{Object.entries(test.capabilities).map(([name, info]) => <div key={name} className="flex min-h-11 items-center justify-between gap-4 py-2 text-sm"><span>{titleCase(name)}</span><span className={info.reachable ? "text-[#167a5b]" : info.resolved === "missing" ? "text-[#986200]" : "text-[#526360]"}>{info.reachable ? "Reachable" : info.error ?? titleCase(info.resolved)}</span></div>)}</div></section>}
      {error && <p role="alert" className="mt-6 text-sm text-[#b42318]">{error}</p>}
      {summary && !summary.validation.ok && <div className="mt-6 rounded-[8px] border border-[#e6c98d] bg-[#fff7e7] p-4"><p className="font-semibold text-[#7d5100]">Configuration incomplete</p><ul className="mt-2 list-disc pl-5 text-sm text-[#654400]">{summary.validation.problems.map((problem) => <li key={problem}>{problem}</li>)}</ul></div>}

      <div className="sticky bottom-4 mt-9 flex flex-wrap items-center justify-between gap-4 rounded-[10px] bg-[#10211f] px-4 py-3 text-white shadow-[0_8px_24px_rgba(16,33,31,.16)]">
        <span className="flex items-center gap-2 text-sm"><ShieldCheck className="h-4 w-4 text-[#76d1cc]" />Secrets stay in the shared server environment.</span>
        <div className="flex gap-2"><button type="button" disabled={busy} onClick={() => handleSave(true)} className="min-h-11 rounded-[6px] border border-white/30 px-4 text-sm font-semibold hover:bg-white/10 disabled:opacity-50">Test connections</button><button type="button" disabled={busy} onClick={() => handleSave(false)} className="inline-flex min-h-11 items-center gap-2 rounded-[6px] bg-[#019393] px-4 text-sm font-semibold text-[#001918] hover:bg-[#27aaa9] disabled:opacity-50">{busy && <LoaderCircle className="h-4 w-4 animate-spin" />}{saved ? "Saved" : "Save configuration"}</button></div>
      </div>
      {saved && summary?.validation.ok && <div role="status" aria-live="polite" className="mt-5 flex items-center justify-between gap-4 rounded-[8px] border border-[#b5dbce] bg-[#edf8f3] p-4"><p className="text-sm text-[#126448]">Live services are configured. The worker will begin autonomous polling.</p><Link href="/" className="font-semibold text-[#006e6e]">Open cockpit →</Link></div>}
    </main>
  );
}
