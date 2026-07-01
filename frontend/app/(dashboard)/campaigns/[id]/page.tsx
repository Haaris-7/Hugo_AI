import { Suspense } from "react";
import { CampaignWorkspace } from "@/components/campaign-workspace";
import { LoadingState } from "@/components/ui";

export default async function CampaignPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <Suspense fallback={<LoadingState />}><CampaignWorkspace id={id} /></Suspense>;
}
