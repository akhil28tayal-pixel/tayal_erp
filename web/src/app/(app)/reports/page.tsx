"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, call } from "@/lib/api";
import { useToast } from "@/components/toast";
import { Empty, ErrorBox, Loading, PageHeader, fmtMoney } from "@/components/ui";
import { problemMessage } from "@/components/orders";

type Row = { key: string; label: string; bags: string; tonnes: string; amount: string; trucks: number; orders: number; dealers: number };
type Report = { from: string; to: string; group: string; rows: Row[]; total: Row; previous: Row & { from: string; to: string } };

const GROUPS = [["day", "Day"], ["dealer", "Dealer"], ["product", "Product"], ["salesperson", "Salesperson"], ["godown", "Godown"]] as const;

const ymd = (d: Date) => d.toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
function preset(k: string): [string, string] {
  const now = new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
  const day = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
  if (k === "today") return [ymd(now), ymd(now)];
  if (k === "yesterday") return [ymd(day(now, -1)), ymd(day(now, -1))];
  if (k === "week") return [ymd(day(now, -((now.getDay() + 6) % 7))), ymd(now)];
  if (k === "last_month") return [ymd(new Date(now.getFullYear(), now.getMonth() - 1, 1)), ymd(new Date(now.getFullYear(), now.getMonth(), 0))];
  if (k === "fy") { const y = now.getMonth() >= 3 ? now.getFullYear() : now.getFullYear() - 1; return [`${y}-04-01`, ymd(now)]; }
  return [ymd(new Date(now.getFullYear(), now.getMonth(), 1)), ymd(now)];
}
const PRESETS = [["today", "Today"], ["yesterday", "Yesterday"], ["week", "This week"], ["month", "This month"], ["last_month", "Last month"], ["fy", "This year (FY)"]] as const;
const fmtD = (s: string) => new Date(s + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });

/** What was delivered, from the ERP's own delivery records (the same quantities that go to Tally). */
export default function ReportsPage() {
  const toast = useToast();
  const [range, setRange] = useState<[string, string]>(preset("month"));
  const [which, setWhich] = useState<string>("month");
  const [group, setGroup] = useState<string>("day");
  const [busy, setBusy] = useState(false);
  const q = useQuery({
    queryKey: ["report-sales", range, group],
    queryFn: () => call(api.GET("/api/v1/reports/sales", { params: { query: { date_from: range[0], date_to: range[1], group } } })) as Promise<Report>,
  });
  const excel = async () => {
    setBusy(true);
    try {
      const { data, response } = await api.GET("/api/v1/reports/sales", {
        params: { query: { date_from: range[0], date_to: range[1], group, format: "xlsx" } }, parseAs: "blob" });
      if (!response.ok || !data) throw new Error(`Download failed (${response.status})`);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(data as Blob);
      a.download = `sales_${range[0]}_${range[1]}_${group}.xlsx`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { toast("bad", problemMessage(e)); } finally { setBusy(false); }
  };
  const r = q.data;
  const max = r ? Math.max(1, ...r.rows.map((x) => Number(x.amount))) : 1;
  const change = (a: string, b: string) => {
    const x = Number(a), y = Number(b);
    if (!y) return null;
    const p = ((x - y) / y) * 100;
    return <span className={p >= 0 ? "text-ok" : "text-bad"}>{p >= 0 ? "▲" : "▼"} {Math.abs(p).toFixed(0)}%</span>;
  };

  return (
    <>
      <PageHeader title="Sales report" sub="What was delivered to dealers, from the delivery records"
        actions={<>
          <Link className="btn-primary" href="/reports/daily">Daily dealer-wise</Link>
          <button className="btn-quiet" disabled={busy || !r?.rows.length} onClick={excel}>{busy ? "Preparing…" : "Download Excel"}</button></>} />

      <div className="panel mb-4 flex flex-wrap items-end gap-3 px-4 py-3">
        <div className="flex flex-wrap gap-1" role="group" aria-label="Period">
          {PRESETS.map(([k, l]) => (
            <button key={k} onClick={() => { setWhich(k); setRange(preset(k)); }}
                    className={`rounded-[6px] px-3 py-1.5 text-[13.5px] ${which === k ? "bg-blue text-white" : "border border-seam hover:border-blue"}`}>{l}</button>))}
        </div>
        <div className="flex items-end gap-2 text-[13px]">
          <label className="text-steel">From <input type="date" className="input mt-0.5 h-9" value={range[0]} max={range[1]}
            onChange={(e) => { setWhich(""); setRange([e.target.value, range[1]]); }} /></label>
          <label className="text-steel">To <input type="date" className="input mt-0.5 h-9" value={range[1]} min={range[0]}
            onChange={(e) => { setWhich(""); setRange([range[0], e.target.value]); }} /></label>
        </div>
      </div>

      {q.error && <ErrorBox error={q.error} />}
      {q.isLoading && <Loading />}
      {r && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
            {([["Bags", Number(r.total.bags).toLocaleString("en-IN"), r.previous.bags],
               ["Tonnes", r.total.tonnes, r.previous.tonnes],
               ["Amount", fmtMoney(r.total.amount), r.previous.amount],
               ["Trucks delivered", String(r.total.trucks), String(r.previous.trucks)]] as const).map(([l, v, prev], i) => (
              <div key={l} className="panel px-5 py-4">
                <div className="text-[13px] font-semibold text-steel">{l}</div>
                <div className="num display mt-1 truncate text-[28px] font-bold leading-none">{v}</div>
                <div className="mt-1.5 text-[12px] text-steel">
                  {change(i === 2 ? r.total.amount : i === 0 ? r.total.bags : i === 1 ? r.total.tonnes : String(r.total.trucks), prev)}
                  {" "}vs {fmtD(r.previous.from)} – {fmtD(r.previous.to)}
                </div>
              </div>))}
          </div>

          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap gap-1" role="tablist" aria-label="Group by">
              {GROUPS.map(([k, l]) => (
                <button key={k} role="tab" aria-selected={group === k} onClick={() => setGroup(k)}
                        className={`rounded-[4px] px-3 py-1.5 text-[13.5px] ${group === k ? "bg-blue text-white" : "text-steel hover:bg-slab"}`}>By {l.toLowerCase()}</button>))}
            </div>
            <span className="text-[13px] text-steel">{fmtD(r.from)} – {fmtD(r.to)} · {r.total.orders} orders · {r.total.dealers} dealers</span>
          </div>
          {r.rows.length === 0 ? <div className="panel"><Empty title="Nothing delivered in this period" hint="Sales count when a truck is marked delivered." /></div> : (
            <div className="panel overflow-x-auto">
              <table className="tbl">
                <thead><tr><th>{GROUPS.find((g) => g[0] === r.group)?.[1]}</th><th className="text-right">Bags</th><th className="text-right">Tonnes</th>
                  <th className="text-right">Amount</th><th className="w-[22%]" aria-label="Share" /><th className="text-right">Trucks</th><th className="text-right">Orders</th></tr></thead>
                <tbody>
                  {r.rows.map((x) => (
                    <tr key={x.key}>
                      <td className="font-semibold">{x.label}</td>
                      <td className="num text-right">{Number(x.bags).toLocaleString("en-IN")}</td>
                      <td className="num text-right">{x.tonnes}</td>
                      <td className="num text-right font-semibold">{fmtMoney(x.amount)}</td>
                      <td><div className="h-2.5 rounded-full bg-blue-tint"><div className="h-2.5 rounded-full bg-blue" style={{ width: `${(Number(x.amount) / max) * 100}%` }} /></div></td>
                      <td className="num text-right">{x.trucks}</td>
                      <td className="num text-right">{x.orders}</td>
                    </tr>))}
                  <tr className="border-t-2 border-ink/20 font-bold">
                    <td>Total</td><td className="num text-right">{Number(r.total.bags).toLocaleString("en-IN")}</td>
                    <td className="num text-right">{r.total.tonnes}</td><td className="num text-right">{fmtMoney(r.total.amount)}</td>
                    <td /><td className="num text-right">{r.total.trucks}</td><td className="num text-right">{r.total.orders}</td>
                  </tr>
                </tbody>
              </table>
            </div>)}
          <p className="mt-2 text-[12px] text-steel">Amount is at the order rate for the quantity actually delivered. TMT and other non-bag items count in tonnes, not bags.</p>
        </>
      )}
    </>
  );
}
