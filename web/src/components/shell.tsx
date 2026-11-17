"use client";

import Link from "next/link";
import { useState } from "react";
import { usePathname } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Drawer, Field } from "@/components/ui";
import { NotificationBell, SearchBox } from "@/components/topbar";

type Item = { href: string; label: string; perm?: string; soon?: string };

// Order follows docs/03 §D. Modules not built yet are listed (so people know they are coming) but not linked.
// Each item shows only to people whose roles allow it.
const NAV: Item[] = [
  { href: "/overview", label: "Overview" },
  { href: "/orders", label: "Orders", perm: "orders.view" },
  { href: "/whatsapp-orders", label: "WhatsApp orders", perm: "orders.view" },
  { href: "/dispatch", label: "Dispatch", perm: "dispatch.view" },
  { href: "/collections", label: "Collections", perm: "collections.view" },
  { href: "/stock", label: "Stock & trucks", perm: "inventory.view" },
  { href: "/purchases", label: "Purchases", perm: "inventory.import" },
  { href: "/tally-sales", label: "Sales in Tally", perm: "tally.view" },
  { href: "/reports/daily", label: "Daily sales", perm: "reports.view" },
  { href: "/reports", label: "Sales report", perm: "reports.view" },
  { href: "/dealers", label: "Dealers", perm: "masters.view" },
  { href: "/godowns", label: "Godowns", perm: "masters.view" },
  { href: "/products", label: "Products", perm: "masters.view" },
  { href: "/vehicles", label: "Vehicles & drivers", perm: "masters.view" },
  { href: "/users", label: "Users", perm: "admin.users" },
  { href: "/settings", label: "Settings", perm: "masters.view" },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { user, companies, company, switchCompany, logout, can } = useAuth();
  const qc = useQueryClient();
  const [pwOpen, setPwOpen] = useState(false);

  return (
    <div className="flex min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:bg-blue focus:text-white focus:px-3 focus:py-2">
        Skip to content
      </a>
      <nav aria-label="Main" className="sticky top-0 hidden print:!hidden h-screen w-[232px] shrink-0 flex-col bg-blue text-white md:flex">
        <div className="px-5 pb-6 pt-5">
          <div className="display text-[18px] font-bold leading-none">Tayal ERP</div>
          <div className="mt-1 text-[12px] text-white/55">Cement &amp; TMT</div>
        </div>
        <ul className="flex-1 space-y-px overflow-y-auto pb-4">
          {NAV.filter((i) => !i.perm || can(i.perm)).map((i) => {
            // the most specific menu item wins ("/reports/daily" over "/reports")
            const active = (path === i.href || path.startsWith(i.href + "/")) &&
              !NAV.some((x) => x.href !== i.href && x.href.startsWith(i.href + "/") && (path === x.href || path.startsWith(x.href + "/")));
            if (i.soon) {
              return (
                <li key={i.href} className="flex items-center justify-between py-2 pl-5 pr-4 text-[13.5px] text-white/35">
                  <span>{i.label}</span><span className="text-2xs">{i.soon}</span>
                </li>
              );
            }
            return (
              <li key={i.href}>
                <Link href={i.href} aria-current={active ? "page" : undefined}
                      className={`relative block py-2 pl-5 pr-4 text-[13.5px] ${
                        active ? "bg-white/[.16] font-semibold text-white" : "text-white/80 hover:bg-white/[.08] hover:text-white"}`}>
                  {active && <span aria-hidden className="absolute inset-y-1 left-0 w-[5px] bg-hivis" />}
                  {i.label}
                </Link>
              </li>
            );
          })}
        </ul>
        <div className="border-t border-white/10 px-5 py-4 text-[12.5px]">
          <div className="truncate font-semibold">{user?.full_name}</div>
          <div className="truncate text-white/55">{company?.roles.map(roleName).join(", ")}</div>
          <div className="mt-2 flex gap-3">
            <button onClick={() => setPwOpen(true)} className="text-white/70 underline-offset-2 hover:text-white hover:underline">Change password</button>
            <button onClick={logout} className="text-white/70 underline-offset-2 hover:text-white hover:underline">Sign out</button>
          </div>
        </div>
      </nav>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-seam bg-slab px-4 print:hidden md:px-8">
          <details className="relative md:hidden">
            <summary className="display cursor-pointer list-none font-bold">Tayal ERP ▾</summary>
            <ul className="absolute left-0 z-30 mt-2 w-56 rounded-[6px] border border-seam bg-slab py-1 shadow-lg">
              {NAV.filter((i) => !i.soon && (!i.perm || can(i.perm))).map((i) => (
                <li key={i.href}><Link href={i.href} className="block px-4 py-2 text-[14px] hover:bg-concrete">{i.label}</Link></li>
              ))}
              <li className="border-t border-seam"><button onClick={() => setPwOpen(true)} className="w-full px-4 py-2 text-left text-[14px] text-steel">Change password</button></li>
              <li><button onClick={logout} className="w-full px-4 py-2 text-left text-[14px] text-steel">Sign out</button></li>
            </ul>
          </details>
          <SearchBox />
          <div className="ml-auto flex items-center gap-2 text-[13px]">
            <NotificationBell />
            <label htmlFor="company" className="hidden text-steel lg:inline">Company</label>
            {companies.length > 1 ? (
              <select id="company" className="input h-8 w-auto py-0" value={company?.company_id ?? ""}
                      onChange={(e) => { switchCompany(e.target.value); qc.clear(); }}>
                {companies.map((c) => <option key={c.company_id} value={c.company_id}>{c.company_name}</option>)}
              </select>
            ) : (
              <span id="company" className="hidden font-semibold lg:inline">{company?.company_name}</span>
            )}
          </div>
        </div>
        <main id="main" className="mx-auto w-full max-w-[1280px] flex-1 px-4 py-6 md:px-8">{children}</main>
      </div>
      {pwOpen && <ChangePassword onClose={() => setPwOpen(false)} onDone={logout} />}
    </div>
  );
}

/** Changing the password signs out every device (this one included), so the user signs in again with it. */
function ChangePassword({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ current: "", next: "", again: "" });
  const [err, setErr] = useState<string | null>(null);
  const m = useMutation({
    mutationFn: () => call(api.POST("/api/v1/auth/password", { body: { current_password: f.current, new_password: f.next } })),
    onSuccess: () => { window.alert("Password changed. Sign in again with the new password."); onDone(); },
    onError: (e) => setErr(e instanceof ApiError ? e.problem.detail : "Failed"),
  });
  const mismatch = f.again.length > 0 && f.again !== f.next;
  return (
    <Drawer title="Change password" open onClose={onClose}>
      <div className="space-y-4">
        <Field label="Current password"><input className="input" type="password" autoComplete="current-password"
          value={f.current} onChange={(e) => setF({ ...f, current: e.target.value })} /></Field>
        <Field label="New password" hint="At least 8 characters"><input className="input" type="password" autoComplete="new-password"
          value={f.next} onChange={(e) => setF({ ...f, next: e.target.value })} /></Field>
        <Field label="New password again" error={mismatch ? "Does not match" : undefined}><input className="input" type="password"
          autoComplete="new-password" value={f.again} onChange={(e) => setF({ ...f, again: e.target.value })} /></Field>
        {err && <p role="alert" className="text-[13px] text-bad">{err}</p>}
        <p className="text-[12.5px] text-steel">You will be signed out on this computer and on your phone.</p>
        <button className="btn-primary" disabled={!f.current || f.next.length < 8 || f.next !== f.again || m.isPending}
                onClick={() => m.mutate()}>Change password</button>
      </div>
    </Drawer>
  );
}

const ROLE_NAMES: Record<string, string> = {
  SUPER_ADMIN: "Super admin", BUSINESS_OWNER: "Owner", OPERATIONS_MANAGER: "Operations", ACCOUNTS_EXECUTIVE: "Accounts",
  SALES_EXECUTIVE: "Sales", COLLECTION_EXECUTIVE: "Collections", GODOWN_SUPERVISOR: "Godown supervisor", DRIVER: "Driver",
};
export const roleName = (code: string) => ROLE_NAMES[code] ?? code;
