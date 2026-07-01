import { Suspense } from "react";
import { CampaignList } from "@/components/campaign-list";
import { LoadingState } from "@/components/ui";

export default function CampaignsPage() { return <Suspense fallback={<LoadingState />}><CampaignList /></Suspense>; }
