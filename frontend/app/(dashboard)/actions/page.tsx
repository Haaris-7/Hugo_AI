import { Suspense } from "react";
import { ActionQueue } from "@/components/action-queue";
import { LoadingState } from "@/components/ui";

export default function ActionsPage() { return <Suspense fallback={<LoadingState />}><ActionQueue /></Suspense>; }
