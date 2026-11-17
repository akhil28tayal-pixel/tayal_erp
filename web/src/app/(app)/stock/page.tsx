"use client";

import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { Badge, Empty, ErrorBox, Loading, PageHeader, fmtQty } from "@/components/ui";
import { DOBadge, ReasonDrawer, SUPPLIER, fmtDate, fmtTime, problemMessage, useStockRefresh } from "@/components/orders";

/** Godown stock as Tally has it (read after every purchase push and Tally sync, or on Refresh), and the factory
 *  trucks in the Direct godown that dealers can be served from. */
export default function StockPage() {
  return <Suspense><StockTabs /></Suspense>;
}

function StockTabs() {
  const [tab, setTab] = useState<"stock" | "trucks">(useSearchParams().get("tab") === "trucks" ? "trucks" : "stock");
  return (
    <>
      <PageHeader title="Stock & trucks" sub="Godown stock from Tally, and Direct trucks waiting to be delivered" />
      <div className="mb-4 flex gap-1" role="tablist">
        {([["stock", "Godown stock"], ["trucks", "Direct trucks"]] as const).map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                  className={`rounded-[4px] px-3 py-1.5 text-[13.5px] ${tab === k ? "bg-blue text-white" : "text-steel hover:bg-slab"}`}>{l}</button>))}
      </div>
      {tab === "stock" ? <Stock /> : <Trucks />}
    </>
  );
}

function Stock() {
  const q = useQuery({ queryKey: ["stock"], queryFn: () => call(api.GET("/api/v1/stock")) });
  const refresh = useStockRefresh(() => q.refetch());
  const [all, setAll] = useState(false);
  if (q.error) return <ErrorBox error={q.error} />;
  if (q.isLoading || !q.data) return <Loading />;
  const s = q.data;
  const running = refresh.busy || s.latest?.status === "RUNNING";
  const godowns = s.godowns.filter((g) => all || g.erp_enabled);
  return (
    <>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-[13.5px]">
        <span className="text-steel">{s.as_of ? <>As per Tally at <b className="text-ink">{fmtTime(s.as_of)}</b></> : "Not read from Tally yet"}
          {s.latest?.status === "FAILED" && <span className="ml-2 text-bad">Last read failed: {s.latest.error}</span>}</span>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-steel"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />
            Show switched-off godowns</label>
          <button className="btn-quiet h-8" disabled={running} onClick={refresh.run}>{running ? "Reading Tally…" : "Refresh from Tally"}</button>
        </div>
      </div>
      {godowns.length === 0 ? <div className="panel"><Empty title="No stock to show" hint={s.as_of ? "Every godown is empty in Tally." : "Press Refresh from Tally."} /></div> : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {godowns.map((g) => (
            <div key={g.godown_id ?? g.name} className="panel">
              <div className="flex items-center justify-between border-b border-seam px-4 py-2.5">
                <h2 className="font-semibold">{g.name}</h2>
                <span className="flex gap-1">{g.kind === "DIRECT" && <Badge tone="warn">Direct</Badge>}
                  {!g.erp_enabled && <Badge>{g.godown_id ? "Switched off" : "Only in Tally"}</Badge>}</span>
              </div>
              <table className="w-full text-[13.5px]">
                <tbody>
                  {g.items.map((it) => (
                    <tr key={it.tally_item} className="border-t border-seam first:border-t-0">
                      <td className="px-4 py-1.5">{it.tally_item}{!it.product_id && <span className="ml-1 text-[12px] text-steel">(not in ERP)</span>}</td>
                      <td className={`num px-4 py-1.5 text-right font-semibold ${Number(it.qty) < 0 ? "text-bad" : ""}`}>
                        {fmtQty(it.qty)} <span className="font-normal text-steel">{it.unit}</span>
                        {Number(it.reserved_base) > 0 && <div className="text-[12px] font-normal text-warn">{fmtQty(it.reserved_base)} {it.base_uom?.toLowerCase()} on trucks at the gate</div>}
                      </td>
                    </tr>))}
                </tbody>
              </table>
            </div>))}
        </div>
      )}
    </>
  );
}

function Trucks() {
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useAuth();
  const [status, setStatus] = useState<"OPEN" | "CLOSED">("OPEN");
  const [closing, setClosing] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["direct-trucks", status], queryFn: () => call(api.GET("/api/v1/direct-trucks", { params: { query: { status } } })) });
  const act = useMutation({
    mutationFn: ({ id, note }: { id: string; note?: string }) => note
      ? call(api.POST("/api/v1/direct-trucks/{truck_id}/close", { params: { path: { truck_id: id } }, body: { note } }))
      : call(api.POST("/api/v1/direct-trucks/{truck_id}/reopen", { params: { path: { truck_id: id } } })),
    onSuccess: (t) => { toast("ok", `${t.truck_no} ${t.status === "OPEN" ? "reopened" : "closed"}`); setClosing(null); qc.invalidateQueries({ queryKey: ["direct-trucks"] }); },
    onError: (e) => toast("bad", problemMessage(e)),
  });
  return (
    <>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-[13.5px]">
        <p className="text-steel">Purchases pushed to the Direct godown. Deliver from them with <b className="text-ink">Dispatch</b> on an approved order.</p>
        <select className="input h-8 w-36" aria-label="Show" value={status} onChange={(e) => setStatus(e.target.value as "OPEN")}>
          <option value="OPEN">Still loaded</option><option value="CLOSED">Done / closed</option>
        </select>
      </div>
      {q.error && <ErrorBox error={q.error} />}
      {q.isLoading && <Loading />}
      {q.data && q.data.length === 0 && <div className="panel"><Empty title={status === "OPEN" ? "No direct trucks waiting" : "None closed yet"}
        hint={status === "OPEN" ? <>They appear here when an invoice to the Direct godown is pushed on <Link className="underline" href="/purchases">Purchases</Link>.</> : undefined} /></div>}
      <div className="grid gap-3 md:grid-cols-2">
        {q.data?.map((t) => (
          <div key={t.id} className="panel px-4 py-3">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="num text-[17px] font-bold">{t.truck_no}</div>
                <div className="text-[12.5px] text-steel">{SUPPLIER[t.supplier] ?? t.supplier} · Inv {t.invoice_no} · {fmtDate(t.invoice_date)}{t.source ? ` · ${t.source.replace(/^\d+\s*-\s*/, "")}` : ""}</div>
              </div>
              {t.status === "OPEN" ? <Badge tone="warn">Loaded</Badge> : <Badge>{t.close_note === "Fully delivered" ? "Delivered" : "Closed"}</Badge>}
            </div>
            <table className="mt-2 w-full text-[13.5px]">
              <tbody>{t.lines.map((l) => (
                <tr key={l.tally_item}><td className="py-0.5">{l.tally_item}</td>
                  <td className="num py-0.5 text-right"><b>{fmtQty(l.left_qty ?? l.qty)}</b> of {fmtQty(l.qty)} {l.uom.toLowerCase()} left</td></tr>))}
              </tbody>
            </table>
            {t.deliveries.length > 0 && (
              <ul className="mt-2 space-y-0.5 border-t border-seam pt-2 text-[12.5px]">
                {t.deliveries.map((d) => (
                  <li key={d.do_id} className="flex items-center justify-between gap-2">
                    <Link className="underline" href={`/dispatch/${d.do_id}`}>{d.do_no}</Link>
                    <span className="truncate text-steel">{d.dealer_name}</span><DOBadge status={d.status} />
                  </li>))}
              </ul>)}
            {t.close_note && t.close_note !== "Fully delivered" && <p className="mt-2 text-[12.5px] text-steel">Closed: {t.close_note}</p>}
            {can("dispatch.plan") && (
              <div className="mt-2 text-right">
                {t.status === "OPEN"
                  ? <button className="text-[12.5px] text-steel underline" onClick={() => setClosing(t.id)}>Close without the ERP</button>
                  : <button className="text-[12.5px] text-steel underline" onClick={() => act.mutate({ id: t.id })}>Reopen</button>}
              </div>)}
          </div>))}
      </div>
      {closing && <ReasonDrawer open title="Close truck" label="Why? (e.g. delivered before the ERP, unloaded at a godown)"
                                onClose={() => setClosing(null)} pending={act.isPending}
                                onSubmit={(note) => act.mutate({ id: closing, note })} />}
    </>
  );
}
