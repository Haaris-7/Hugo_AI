export type LearningMode = "database" | "database_and_skill_patch";
export type Platform = "tiktok" | "instagram" | "youtube";
export type OperationMode = "strategy_creators" | "strategy_creators_payments" | "full_autonomy";
export type CompensationComponent = {
  kind: "base" | "cpm" | "engagement" | "affiliate";
  rate_cents: number;
};

export type CampaignSummary = {
  id: string;
  run_id: string;
  brand_id: string;
  brand_name: string;
  name: string;
  goal: string;
  platform: Platform;
  budget_cents: number;
  per_creator_cap_cents: number;
  payout_model: string;
  compensation: { pricing_mode?: string; components: CompensationComponent[] };
  compensation_source: string;
  compensation_locked: boolean;
  operation_mode: OperationMode;
  measurement_window_hours: number;
  learning_mode: LearningMode;
  status: string;
  version: number;
  views: number;
  engagements: number;
  conversions: number;
  metrics_recorded: boolean;
  created_at: string;
  updated_at: string;
  is_demo?: boolean;
};

export type ActionItem = {
  id: string;
  type: "strategy" | "deal" | "service_spend" | "payout";
  campaign_id: string;
  campaign_name: string;
  title: string;
  detail: string;
  amount_cents: number;
  expected_version: number;
  created_at: string;
};

export type Overview = {
  campaigns_total: number;
  campaigns_active: number;
  funded_cents: number;
  transferred_cents: number;
  pending_actions: number;
  campaigns: CampaignSummary[];
  actions: ActionItem[];
  events: Array<{
    campaign_id: string;
    campaign_name: string;
    type: string;
    payload: Record<string, unknown>;
    created_at: string;
  }>;
};

export type AlgorithmPlaybook = {
  id: string;
  platform: string;
  signals: Array<{ signal?: string; effect?: string; [key: string]: unknown }>;
  sources: Array<{ url?: string; title?: string; [key: string]: unknown }>;
  confidence: number;
  updated_at: string;
};

export type Brand = {
  id: string;
  name: string;
  niche: string;
  website?: string;
  campaign_count: number;
};

export type LearningRun = {
  id: string;
  campaign_id: string;
  campaign_name?: string;
  run_id: string;
  status: string;
  baseline_status: string;
  summary?: string;
  evidence: Record<string, unknown>;
  database_updates: {
    strategy_prior?: {
      niche: string;
      creator_tier: string;
      before?: Record<string, number>;
      after?: Record<string, number>;
    };
    creator_reputations?: Array<{
      creator_id: string;
      handle: string;
      before: Record<string, number>;
      after: Record<string, number>;
    }>;
  };
  patch_status: string;
  patch_summary?: string;
  skill_version?: number;
  patch_error?: string;
  error?: string;
  created_at: string;
  updated_at: string;
};

export type CampaignState = {
  campaign: CampaignSummary & {
    next_action: string;
  };
  strategy: null | {
    id: string;
    creator_tier: string;
    target_creators: number;
    target_rate_cents: number;
    primary_allocation: number;
    challenger_allocation: number;
    rationale: string;
    projected_cost_per_result: number;
    skill_version: number;
    approved: boolean;
  };
  algorithm_playbook: AlgorithmPlaybook | null;
  funding: null | {
    id: string;
    status: string;
    amount_cents: number;
    checkout_url?: string;
    payment_intent_id?: string;
    source_charge_id?: string;
  };
  experiments: Array<{
    id: string;
    name: string;
    hypothesis: string;
    variant: string;
    allocation: number;
    status: string;
    metrics: Record<string, number>;
  }>;
  service_spend: Array<{
    id: string;
    provider: string;
    amount_cents: number;
    status: string;
    context: string;
  }>;
  deals: Deal[];
  approval_requests: Array<{
    id: string;
    resource_type: string;
    resource_id: string;
    status: string;
    expires_at: string;
  }>;
  payouts: Array<{
    id: string;
    deal_id: string;
    creator_id: string;
    payout_model: string;
    component: string;
    amount_cents: number;
    measured_metric?: number;
    status: string;
    transfer_id?: string;
  }>;
  ledger: {
    funded_cents: number;
    spent_cents: number;
    remaining_cents: number;
    entries: LedgerEntry[];
  };
  learning: LearningRun | null;
  events: Array<{ type: string; payload: Record<string, unknown>; created_at: string }>;
};

export type Deal = {
  id: string;
  creator_id: string;
  handle: string;
  email?: string;
  followers: number;
  engagement_rate: number;
  fake_follower_percent: number;
  stripe_onboarded: boolean;
  fit_score: number;
  status: string;
  agreed_rate_cents?: number;
  terms_accepted: boolean;
  reputation: number;
  revision_count: number;
  compensation: { pricing_mode?: string; components: CompensationComponent[] };
  draft_approved: boolean;
  final_approved: boolean;
  replacement_attempt: number;
  messages: Array<{
    id: string;
    direction: string;
    channel: string;
    body: string;
    intent?: string;
    proposed_rate_cents?: number;
    created_at: string;
  }>;
  deliverables: Array<{
    id: string;
    caption: string;
    media_url?: string;
    post_url?: string;
    stage: "draft" | "final";
    qa_status: string;
    created_at: string;
    checks: Array<{
      passed: boolean;
      severity: string;
      findings: Array<{ code: string; message: string }>;
      model: string;
    }>;
  }>;
};

export type LedgerEntry = {
  id: string;
  campaign_id?: string;
  campaign_name?: string;
  entry_type: string;
  amount_cents: number;
  reference_id: string;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type HermesTask = {
  id: string;
  campaign_id: string;
  deal_id: string | null;
  task_type: string;
  status: string;
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
  error: string | null;
  attempt: number;
  lease_expires_at: string | null;
  evidence: Record<string, unknown>;
  created_at: string;
};

export type HermesTaskPreflight = {
  pending: number;
  claimed: number;
  failed: number;
  should_claim: boolean;
};
