"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { Empty, ErrorBox, Loading, PageHeader, fmtMoney } from "@/components/ui";
import { problemMessage } from "@/components/orders";

type Line = { product: string; qty: string; unit: string; rate: string; rate_unit: string; amount: string; orders: string[] };
type Dealer = { dealer_id: string; dealer: string; orders: string[]; trucks: number; amount: string; bags: string; tonnes: string; lines: Line[] };
type Daily = { from: string; to: string; dealers: Dealer[]; products: { product: string; unit: string; qty: string; amount: string }[];
  total: { amount: string; bags: string; tonnes: string; trucks: number; dealers: number } };

const ymd = (d: Date) => d.toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
const shift = (s: string, n: number) => { const d = new Date(s + "T00:00:00"); d.setDate(d.getDate() + n); return ymd(d); };
const long = (s: string) => new Date(s + "T00:00:00").toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
const num = (v: string) => Number(v).toLocaleString("en-IN", { maximumFractionDigits: 3 });
const rate = (v: string) => "₹" + Number(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Daily report: each dealer's deliveries for the day with product, quantity, rate and amount. */
export default function DailyReportPage() {
  const toast = useToast();
  const { company } = useAuth();
  const today = ymd(new Date());
  const [day, setDay] = useState(today);
  const [busy, setBusy] = useState(false);
  const q = useQuery({
    queryKey: ["report-daily", day],
    queryFn: () => call(api.GET("/api/v1/reports/daily", { params: { query: { day } } })) as Promise<Daily>,
  });
  const excel = async () => {
    setBusy(true);
    try {
      const { data, response } = await api.GET("/api/v1/reports/daily", { params: { query: { day, format: "xlsx" } }, parseAs: "blob" });
      if (!response.ok || !data) throw new Error(`Download failed (${response.status})`);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(data as Blob);
      a.download = `dealerwise_sales_${day}.xlsx`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { toast("bad", problemMessage(e)); } finally { setBusy(false); }
  };
  const r = q.data;

  return (
    <>
      <div className="print:hidden">
        <PageHeader title="Daily sales — dealer-wise" back={{ href: "/reports", label: "Sales report" }}
          sub="What each dealer received that day: product, quantity, rate and amount (from the delivery records)"
          actions={<>
            <button className="btn-quiet" disabled={busy || !r?.dealers.length} onClick={excel}>{busy ? "Preparing…" : "Download Excel"}</button>
            <button className="btn-primary" disabled={!r?.dealers.length} onClick={() => window.print()}>Print / PDF</button>
          </>} />
        <div className="panel mb-4 flex flex-wrap items-center gap-2 px-4 py-3">
          <button className="btn-quiet h-10 px-4" onClick={() => setDay(shift(day, -1))} aria-label="Previous day">← Previous day</button>
          <input type="date" className="input h-10 w-44" value={day} max={today} onChange={(e) => e.target.value && setDay(e.target.value)} aria-label="Day" />
          <button className="btn-quiet h-10 px-4" disabled={day >= today} onClick={() => setDay(shift(day, 1))} aria-label="Next day">Next day →</button>
          {day !== today && <button className="text-[13px] text-blue underline" onClick={() => setDay(today)}>Today</button>}
        </div>
      </div>

      {q.error && <ErrorBox error={q.error} />}
      {q.isLoading && <Loading />}
      {r && (
        <article>
          <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
            <div>
              <div className="text-[13px] text-steel">{company?.company_name}</div>
              <h2 className="display text-[22px] font-bold">{long(r.from)}</h2>
            </div>
            <div className="flex flex-wrap gap-5 text-right">
              <div><div className="text-[12px] text-steel">Dealers</div><div className="num text-[20px] font-bold">{r.total.dealers}</div></div>
              <div><div className="text-[12px] text-steel">Bags</div><div className="num text-[20px] font-bold">{num(r.total.bags)}</div></div>
              <div><div className="text-[12px] text-steel">Tonnes</div><div className="num text-[20px] font-bold">{r.total.tonnes}</div></div>
              <div><div className="text-[12px] text-steel">Trucks</div><div className="num text-[20px] font-bold">{r.total.trucks}</div></div>
              <div><div className="text-[12px] text-steel">Total</div><div className="num text-[20px] font-bold">{fmtMoney(r.total.amount)}</div></div>
            </div>
          </header>

          {r.dealers.length === 0 ? <div className="panel"><Empty title="Nothing delivered on this day" hint="Sales count when a truck is marked delivered." /></div> : (
            <div className="panel overflow-x-auto print:border-0">
              <table className="tbl text-[13.5px]">
                <thead><tr><th>Dealer</th><th>Product</th><th className="text-right">Quantity</th><th className="text-right">Rate</th>
                  <th className="text-right">Amount</th><th className="print:hidden">Order</th></tr></thead>
                <tbody>
                  {r.dealers.map((d) => [
                    ...d.lines.map((l, i) => (
                      <tr key={d.dealer_id + i} className={i === 0 ? "border-t-2 border-seam" : ""}>
                        <td className="font-semibold">{i === 0 ? <Link className="hover:underline" href={`/dealers/${d.dealer_id}`}>{d.dealer}</Link> : ""}</td>
                        <td>{l.product}</td>
                        <td className="num whitespace-nowrap text-right">{num(l.qty)} <span className="text-steel">{l.unit.toLowerCase()}</span></td>
                        <td className="num whitespace-nowrap text-right">{rate(l.rate)} <span className="text-steel">/{l.rate_unit.toLowerCase()}</span></td>
                        <td className="num text-right">{fmtMoney(l.amount)}</td>
                        <td className="text-[12.5px] text-steel print:hidden">{l.orders.join(", ")}</td>
                      </tr>)),
                    (
                      <tr key={d.dealer_id + "t"} className="bg-blue-tint/50 font-semibold">
                        <td className="text-steel">{d.trucks} truck{d.trucks === 1 ? "" : "s"}{Number(d.bags) ? ` · ${num(d.bags)} bags` : ""}</td>
                        <td /><td /><td className="text-right text-steel">Dealer total</td>
                        <td className="num text-right">{fmtMoney(d.amount)}</td><td className="print:hidden" />
                      </tr>),
                  ])}
                  <tr className="border-t-2 border-ink/30 text-[15px] font-bold">
                    <td>Grand total</td><td>{r.total.dealers} dealer{r.total.dealers === 1 ? "" : "s"}</td><td className="num text-right">{num(r.total.bags)} bags</td>
                    <td className="num text-right text-steel">{r.total.tonnes} t</td><td className="num text-right">{fmtMoney(r.total.amount)}</td><td className="print:hidden" />
                  </tr>
                </tbody>
              </table>
            </div>)}

          {r.products.length > 0 && (
            <section className="mt-5 max-w-xl break-inside-avoid">
              <h3 className="display mb-2 text-[15px] font-bold">By product</h3>
              <div className="panel">
                <table className="tbl text-[13.5px]">
                  <thead><tr><th>Product</th><th className="text-right">Quantity</th><th className="text-right">Amount</th></tr></thead>
                  <tbody>{r.products.map((p) => (
                    <tr key={p.product + p.unit}><td>{p.product}</td>
                      <td className="num text-right">{num(p.qty)} <span className="text-steel">{p.unit.toLowerCase()}</span></td>
                      <td className="num text-right">{fmtMoney(p.amount)}</td></tr>))}</tbody>
                </table>
              </div>
            </section>)}
          <p className="mt-3 text-[12px] text-steel">Quantity is what was delivered, in the unit the order was booked in; the rate is the order&apos;s rate.</p>
        </article>
      )}
    </>
  );
}
