"use client";

import Link from "next/link";
import { cloneElement, isValidElement, useId } from "react";
import { ApiError } from "@/lib/api";

export function PageHeader({ title, sub, actions, back }: {
  title: string; sub?: React.ReactNode; actions?: React.ReactNode; back?: { href: string; label: string };
}) {
  return (
    <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        {back && <Link href={back.href} className="mb-1 inline-block text-[13px] text-steel hover:text-ink">← {back.label}</Link>}
        <h1 className="display text-[26px] font-bold leading-tight">{title}</h1>
        {sub && <p className="mt-1 text-[13.5px] text-steel">{sub}</p>}
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </header>
  );
}

export function Badge({ tone = "neutral", children }: { tone?: "ok" | "warn" | "bad" | "neutral"; children: React.ReactNode }) {
  const cls = {
    ok: "bg-ok/10 text-ok", warn: "bg-warn/10 text-warn", bad: "bg-bad/10 text-bad", neutral: "bg-concrete text-steel",
  }[tone];
  return <span className={`inline-block rounded-[3px] px-1.5 py-0.5 text-[12px] font-semibold ${cls}`}>{children}</span>;
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="px-4 py-10 text-center text-steel" aria-busy="true">{what}…</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof ApiError ? error.problem.detail : "Could not reach the server. Check the API is running.";
  return <div className="rounded-[6px] border border-bad/30 bg-bad/5 px-4 py-3 text-[13.5px] text-bad">{msg}</div>;
}

export function Empty({ title, hint }: { title: string; hint?: React.ReactNode }) {
  return (
    <div className="px-6 py-12 text-center">
      <p className="font-semibold">{title}</p>
      {hint && <p className="mt-1 text-[13.5px] text-steel">{hint}</p>}
    </div>
  );
}

export function Pager({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="flex items-center justify-between px-3 py-2 text-[13px] text-steel">
      <span className="num">{total === 0 ? "No rows" : `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} of ${total}`}</span>
      <div className="flex gap-1">
        <button className="btn-quiet h-8" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
        <button className="btn-quiet h-8" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
      </div>
    </div>
  );
}

/** Label + control linked by id (not by wrapping), so the field's accessible name is just its label. */
export function Field({ label, error, children, hint }: { label: string; error?: string; hint?: string; children: React.ReactElement<{ id?: string; "aria-describedby"?: string }> }) {
  const id = useId();
  const noteId = `${id}-note`;
  const control = isValidElement(children)
    ? cloneElement(children, { id: children.props.id ?? id, "aria-describedby": hint || error ? noteId : undefined })
    : children;
  return (
    <div>
      <label htmlFor={isValidElement(children) ? (children.props.id ?? id) : undefined} className="label">{label}</label>
      {control}
      {hint && !error && <span id={noteId} className="mt-1 block text-[12px] text-steel">{hint}</span>}
      {error && <span id={noteId} role="alert" className="mt-1 block text-[12px] text-bad">{error}</span>}
    </div>
  );
}

/** Maps server validation errors (problem+json `errors[]`) onto form fields. */
export function fieldErrors(e: unknown): Record<string, string> {
  if (!(e instanceof ApiError)) return {};
  return Object.fromEntries((e.problem.errors ?? []).map((x) => [x.field, x.message]));
}

export function Drawer({ title, open, onClose, children }: { title: string; open: boolean; onClose: () => void; children: React.ReactNode }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-ink/30" onMouseDown={onClose}
         onKeyDown={(e) => e.key === "Escape" && onClose()}>
      <aside role="dialog" aria-modal="true" aria-label={title}
             className="h-full w-full max-w-md overflow-y-auto border-l border-seam bg-slab p-6 shadow-xl"
             onMouseDown={(e) => e.stopPropagation()}>
        <div className="mb-5 flex items-center justify-between">
          <h2 className="display text-[19px] font-bold">{title}</h2>
          <button className="btn-quiet h-8" onClick={onClose}>Close</button>
        </div>
        {children}
      </aside>
    </div>
  );
}

export const fmtQty = (v: string | number | null | undefined, digits = 3) =>
  v === null || v === undefined || v === "" ? "—"
    : Number(v).toLocaleString("en-IN", { maximumFractionDigits: digits });

export const fmtMoney = (v: string | number | null | undefined) =>
  v === null || v === undefined || v === "" ? "—"
    : "₹" + Number(v).toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
