"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { Badge, Drawer, Empty, ErrorBox, Field, Loading, PageHeader, fmtMoney } from "@/components/ui";
import { fmtDate, fmtTime, problemMessage } from "@/components/orders";

/* Delivered orders go to Tally as Optional Sales vouchers. They change nothing in Tally until an admin opens one
   there and makes it Regular (approve) — or deletes it (reject). "Check Tally" reads that back. */

const STATUS: Record<string, [string, "ok" | "warn" | "bad" | "neutral"]> = {
  WAITING_APPROVAL: ["Waiting for approval in Tally", "warn"],
  APPROVED: ["Approved in Tally", "ok"],
  REJECTED: ["Rejected in Tally", "bad"],
  FAILED: ["Not sent", "bad"],
  PENDING: ["Sending…", "neutral"],
};
const TABS = ["WAITING_APPROVAL", "FAILED", "REJECTED", "APPROVED"] as const;

export default function TallySalesPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useAuth();
  const [tab, setTab] = useState<string>("WAITING_APPROVAL");
  const [xml, setXml] = useState<string | null>(null);
  const [settings, setSettings] = useState(false);
  const q = useQuery({ queryKey: ["tally-sales", tab], queryFn: () => call(api.GET("/api/v1/tally/sales", { params: { query: { status: tab } } })) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["tally-sales"] });

  const check = useMutation({
    mutationFn: () => call(api.POST("/api/v1/tally/sales/check")) as Promise<Record<string, number>>,
    onSuccess: (r) => {
      refresh();
      toast("ok", r.checked ? `${r.approved ?? 0} approved, ${r.rejected ?? 0} rejected, ${r.waiting ?? 0} still waiting` : "Nothing is waiting in Tally");
    },
    onError: (e) => toast("bad", problemMessage(e)),
  });
  const push = useMutation({
    mutationFn: (ids: string[] | null) => call(api.POST("/api/v1/tally/sales/push", { body: { ids } })) as Promise<Record<string, unknown>>,
    onSuccess: (r) => {
      refresh();
      if (r.error) toast("bad", String(r.error));
      else toast(Number(r.failed) ? "bad" : "ok", `${r.pushed} sent to Tally${Number(r.failed) ? `, ${r.failed} failed` : ""}`);
    },
    onError: (e) => toast("bad", problemMessage(e)),
  });
  const showXml = async (id: string) => {
    try { setXml((await call(api.GET("/api/v1/tally/sales/{sale_id}/xml", { params: { path: { sale_id: id } } }))).xml as string); }
    catch (e) { toast("bad", problemMessage(e)); }
  };

  const d = q.data;
  return (
    <>
      <PageHeader title="Sales in Tally"
        sub="Delivered orders are sent to Tally as Optional vouchers. They count in Tally only after an admin approves them there."
        actions={<>
          {can("tally.sync") && (d?.counts.FAILED ?? 0) + (d?.counts.PENDING ?? 0) > 0 &&
            <button className="btn-quiet" disabled={push.isPending} onClick={() => push.mutate(null)}>Send pending now</button>}
          <button className="btn-primary" disabled={check.isPending} onClick={() => check.mutate()}>
            {check.isPending ? "Reading Tally…" : "Check Tally for approvals"}</button>
        </>} />

      <details className="panel mb-4 px-4 py-3 text-[13.5px]">
        <summary className="cursor-pointer font-semibold">How the admin approves in Tally</summary>
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-steel">
          <li>In TallyPrime open <b className="text-ink">Display More Reports → Exception Reports → Optional Vouchers</b>
            (or the Day Book with optional vouchers shown).</li>
          <li>Open the voucher (its reference is the ERP order number), check it, and press <b className="text-ink">Ctrl+L</b> to
            turn off “Optional”, then save. It now counts in stock and the dealer’s balance — <b className="text-ink">approved</b>.</li>
          <li>To <b className="text-ink">reject</b>, delete the voucher in Tally (Alt+D).</li>
          <li>Back here press <b className="text-ink">Check Tally for approvals</b>.</li>
        </ol>
        <p className="mt-2 text-steel">Sales ledger: <b className="text-ink">{d?.sales_ledger || "the only ledger under Sales Accounts"}</b> ·
          voucher type <b className="text-ink">{d?.voucher_type || "Sales"}</b>
          {can("admin.settings") && <> · <button className="underline" onClick={() => setSettings(true)}>change</button></>}</p>
      </details>

      <div className="mb-3 flex flex-wrap gap-1" role="tablist">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                  className={`rounded-[4px] px-3 py-1.5 text-[13.5px] ${tab === t ? "bg-blue text-white" : "text-steel hover:bg-slab"}`}>
            {STATUS[t][0].replace(" in Tally", "")} <span className="num opacity-70">{d?.counts[t] ?? 0}</span>
          </button>))}
      </div>
      {q.error && <ErrorBox error={q.error} />}
      {q.isLoading && <Loading />}
      {d && (d.items.length === 0 ? <div className="panel"><Empty title="Nothing here" hint={tab === "WAITING_APPROVAL" ? "Orders appear here once they are fully delivered." : undefined} /></div> : (
        <div className="panel overflow-x-auto">
          <table className="w-full text-[13.5px]">
            <thead className="bg-concrete/60 text-left text-[12px] text-steel">
              <tr><th className="px-3 py-2">Order</th><th className="px-3 py-2">Dealer</th><th className="px-3 py-2">Voucher date</th>
                <th className="px-3 py-2 text-right">Amount</th><th className="px-3 py-2">Status</th><th /></tr>
            </thead>
            <tbody>
              {d.items.map((s) => (
                <tr key={s.id} className="border-t border-seam align-top">
                  <td className="num px-3 py-2 font-semibold"><Link className="hover:underline" href={`/orders/${s.order_id}`}>{s.order_no}</Link></td>
                  <td className="px-3 py-2">{s.dealer_name}</td>
                  <td className="px-3 py-2">{fmtDate(s.voucher_date)}</td>
                  <td className="num px-3 py-2 text-right">{fmtMoney(s.amount)}</td>
                  <td className="px-3 py-2"><Badge tone={STATUS[s.status]?.[1] ?? "neutral"}>{STATUS[s.status]?.[0] ?? s.status}</Badge>
                    {s.tally_voucher_no && <div className="text-[12px] text-steel">Tally voucher {s.tally_voucher_no}</div>}
                    {s.error && <div className="max-w-[340px] text-[12px] text-bad">{s.error}</div>}
                    <div className="text-[12px] text-steel">{s.decided_at ? `Decided ${fmtTime(s.decided_at)}` : s.pushed_at ? `Sent ${fmtTime(s.pushed_at)}` : ""}</div></td>
                  <td className="whitespace-nowrap px-3 py-2 text-right">
                    <button className="text-[12.5px] text-steel underline" onClick={() => showXml(s.id)}>XML</button>
                    {can("tally.sync") && (s.status === "FAILED" || s.status === "REJECTED") &&
                      <button className="ml-2 text-[12.5px] text-steel underline" disabled={push.isPending}
                              onClick={() => push.mutate([s.id])}>{s.status === "FAILED" ? "Retry" : "Send again"}</button>}
                  </td>
                </tr>))}
            </tbody>
          </table>
        </div>
      ))}
      {xml !== null && (
        <Drawer title="Tally XML" open onClose={() => setXml(null)}>
          <pre className="max-h-[75vh] overflow-auto whitespace-pre-wrap rounded-[6px] bg-concrete p-3 text-[11.5px]">{xml || "Not built yet"}</pre>
        </Drawer>
      )}
      {settings && <SalesSettings current={d?.sales_ledger ?? ""} vtype={d?.voucher_type ?? "Sales"}
                                  onClose={() => setSettings(false)} onSaved={refresh} />}
    </>
  );
}

function SalesSettings({ current, vtype, onClose, onSaved }: { current: string; vtype: string; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [ledger, setLedger] = useState(current);
  const [type, setType] = useState(vtype);
  const ledgers = useQuery({ queryKey: ["tally-sales-ledgers"], queryFn: () => call(api.GET("/api/v1/tally/sales-ledgers")) as Promise<{ ledgers: string[] }> });
  const save = useMutation({
    mutationFn: async () => {
      await call(api.PUT("/api/v1/settings/{key}", { params: { path: { key: "tally.sales_ledger" } }, body: { value: ledger } }));
      await call(api.PUT("/api/v1/settings/{key}", { params: { path: { key: "tally.sales_voucher_type" } }, body: { value: type || "Sales" } }));
    },
    onSuccess: () => { toast("ok", "Saved"); onSaved(); onClose(); },
    onError: (e) => toast("bad", problemMessage(e)),
  });
  return (
    <Drawer title="Sales vouchers in Tally" open onClose={onClose}>
      <div className="space-y-4">
        <Field label="Sales ledger" hint={ledgers.error ? "Could not read Tally's ledgers" : "Ledgers under Sales Accounts in Tally"}>
          <select className="input" value={ledger} onChange={(e) => setLedger(e.target.value)}>
            <option value="">The only ledger under Sales Accounts</option>
            {ledger && !ledgers.data?.ledgers.includes(ledger) && <option value={ledger}>{ledger}</option>}
            {ledgers.data?.ledgers.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        </Field>
        <Field label="Voucher type"><input className="input" value={type} onChange={(e) => setType(e.target.value)} /></Field>
        <button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>Save</button>
      </div>
    </Drawer>
  );
}
