"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  BrainCircuit,
  ChevronRight,
  CircleCheck,
  CircleDollarSign,
  LayoutDashboard,
  ListChecks,
  Menu,
  Plus,
  Settings2,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { HugoBrand } from "@/components/brand";

const navigation = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/campaigns", label: "Campaigns", icon: Activity },
  { href: "/actions", label: "Action queue", icon: ListChecks },
  { href: "/finance", label: "Finance", icon: CircleDollarSign },
  { href: "/learning", label: "Learning", icon: BrainCircuit },
  { href: "/system", label: "System", icon: Settings2 },
];

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const { data, isLoading } = useQuery<{ services: Array<{ status: string }> }>({
    queryKey: ["system-status"],
    queryFn: () => api("/v1/system/status"),
    refetchInterval: 5_000,
  });
  const healthy = Boolean(data?.services.every((service) => !["unavailable", "attention"].includes(service.status)));
  const activeLabel = navigation.find(({ href }) => href === "/" ? pathname === "/" : pathname.startsWith(href))?.label ?? "Workspace";

  useEffect(() => setOpen(false), [pathname]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    previousFocusRef.current = document.activeElement as HTMLElement;
    const drawer = drawerRef.current;
    if (!drawer) return;
    const focusable = Array.from(drawer.querySelectorAll<HTMLElement>(FOCUSABLE));
    focusable[0]?.focus();

    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    drawer.addEventListener("keydown", trap);
    return () => {
      drawer.removeEventListener("keydown", trap);
      previousFocusRef.current?.focus();
      menuButtonRef.current?.focus();
    };
  }, [open]);

  return (
    <div className="min-h-screen bg-[#f5f7f7] lg:grid lg:grid-cols-[236px_minmax(0,1fr)]">
      <a href="#main-content" className="fixed left-4 top-3 z-[60] -translate-y-20 rounded-[6px] bg-[#10211f] px-4 py-2 text-sm font-semibold text-white transition-transform focus:translate-y-0">
        Skip to main content
      </a>
      <header className="fixed inset-x-0 top-0 z-30 flex h-16 items-center justify-between border-b border-[#dce4e3] bg-white/95 px-4 lg:hidden">
        <button ref={menuButtonRef} onClick={() => setOpen(true)} className="grid h-11 w-11 place-items-center rounded-[6px] hover:bg-[#eef2f2]" aria-label="Open navigation">
          <Menu className="h-5 w-5" />
        </button>
        <HugoBrand compact />
        <span className="w-11" aria-hidden />
      </header>

      {open && <button className="fixed inset-0 z-40 bg-[#10211f]/30 lg:hidden" onClick={() => setOpen(false)} aria-label="Close navigation overlay" />}

      <aside
        ref={drawerRef}
        className={cn(
        "fixed inset-y-0 left-0 z-50 flex w-[236px] flex-col border-r border-[#dce4e3] bg-[#fbfcfc] transition-transform duration-200 lg:sticky lg:top-0 lg:h-screen lg:translate-x-0",
        open ? "translate-x-0" : "-translate-x-full",
      )}>
        <div className="flex h-[74px] items-center justify-between border-b border-[#dce4e3] px-5">
          <Link href="/" className="rounded-[6px]" aria-label="Hugo overview"><HugoBrand /></Link>
          <button onClick={() => setOpen(false)} className="grid h-11 w-11 place-items-center rounded-[6px] hover:bg-[#eef2f2] lg:hidden" aria-label="Close navigation"><X className="h-5 w-5" /></button>
        </div>

        <div className="px-4 pb-2 pt-5">
          <p className="px-2 text-xs font-semibold text-[#687975]">Operator workspace</p>
        </div>
        <nav className="flex-1 px-3" aria-label="Primary navigation">
          {navigation.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "mb-1 flex min-h-11 items-center gap-3 rounded-[6px] px-3 text-sm font-medium text-[#526360] transition-colors duration-150 hover:bg-[#eef2f2] hover:text-[#10211f]",
                  active && "bg-[#e6f5f4] font-semibold text-[#10211f] shadow-[inset_2px_0_#019393] hover:bg-[#dcefed]",
                )}
              >
                <Icon className={cn("h-[18px] w-[18px]", active && "text-[#019393]")} strokeWidth={1.8} aria-hidden />
                {label}
              </Link>
            );
          })}
        </nav>

        <div className="border-t border-[#dce4e3] p-3">
          <Link href="/campaigns/new" className="flex min-h-11 items-center justify-between rounded-[6px] bg-[#006e6e] px-3 text-sm font-semibold text-white transition-colors hover:bg-[#005b5b]">
            <span className="flex items-center gap-2"><Plus className="h-4 w-4" />New campaign</span><ChevronRight className="h-4 w-4" />
          </Link>
          <Link href="/system" className="mt-3 flex min-h-11 items-center gap-2 rounded-[6px] px-2 text-xs text-[#526360] hover:bg-[#eef2f2]">
            <CircleCheck className={cn("h-4 w-4", healthy ? "text-[#167a5b]" : "text-[#986200]")} aria-hidden />
            {isLoading ? "Checking systems" : healthy ? "Systems operational" : "System attention needed"}
          </Link>
        </div>
      </aside>

      <main id="main-content" tabIndex={-1} className="min-w-0 px-4 pb-16 pt-24 sm:px-7 lg:px-9 lg:pt-7 xl:px-12">
        <div className="page-enter mx-auto max-w-[1540px]">
          <div className="mb-6 hidden items-center justify-between lg:flex">
            <p className="text-xs font-medium text-[#687975]">Hugo / <span className="text-[#354542]">{activeLabel}</span></p>
            <span className="flex items-center gap-2 text-xs text-[#687975]"><span className="h-1.5 w-1.5 rounded-full bg-[#019393]" />Live data · refreshes every 3–5 sec</span>
          </div>
          {children}
        </div>
      </main>
    </div>
  );
}
