"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Empty, ErrorBox, Loading, PageHeader, fmtMoney } from "@/components/ui";

type Line = { date: string; type: string; number: string; reference: string; narration: string; debit: string; credit: string; balance: string };
type Statement = { dealer: string; ledger: string; from: string; to: string; opening: string; closing: string; total_debit: string; total_credit: string; rows: Line[] };

const ymd = (d: Date) => d.toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
const fmtD = (s: string) => new Date(s + "T00:00:00").toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
/** Dr = dealer owes us, Cr = we owe the dealer (advance). */
const drcr = (v: string) => { const n = Number(v); return n === 0 ? "₹0" : `${fmtMoney(String(Math.abs(n)))} ${n > 0 ? "Dr" : "Cr"}`; };
const amt = (v: string) => (Number(v) ? Number(v).toLocaleString("en-IN", { minimumFractionDigits: 2 }) : "");

/** The dealer's ledger in Tally for a period. Print → Save as PDF to send it to the dealer. */
export default function StatementPage() {
  const { id } = useParams<{ id: string }>();
  const { company } = useAuth();
  const today = new Date();
  const [from, setFrom] = useState(ymd(new Date(today.getFullYear(), today.getMonth() - 2, 1)));
  const [to, setTo] = useState(ymd(today));
  const q = useQuery({
    queryKey: ["statement", id, from, to], retry: false,
    queryFn: () => call(api.GET("/api/v1/dealers/{dealer_id}/statement", { params: { path: { dealer_id: id }, query: { date_from: from, date_to: to } } })) as Promise<Statement>,
  });
  const s = q.data;

  return (
    <>
      <div className="print:hidden">
        <PageHeader back={{ href: `/dealers/${id}`, label: s?.dealer ?? "Dealer" }} title="Statement"
          sub="Read from Tally: opening balance, every bill and payment, closing balance"
          actions={<button className="btn-primary" disabled={!s} onClick={() => window.print()}>Print / Save as PDF</button>} />
        <div className="panel mb-4 flex flex-wrap items-end gap-3 px-4 py-3 text-[13px]">
          <label className="text-steel">From <input type="date" className="input mt-0.5 h-9" value={from} max={to} onChange={(e) => setFrom(e.target.value)} /></label>
          <label className="text-steel">To <input type="date" className="input mt-0.5 h-9" value={to} min={from} onChange={(e) => setTo(e.target.value)} /></label>
          <div className="flex gap-1">
            {[["This month", 0], ["Last 3 months", 2], ["Last 6 months", 5]].map(([l, m]) => (
              <button key={l} className="rounded-[6px] border border-seam px-3 py-1.5 hover:border-blue"
                      onClick={() => { setFrom(ymd(new Date(today.getFullYear(), today.getMonth() - Number(m), 1))); setTo(ymd(today)); }}>{l}</button>))}
          </div>
        </div>
      </div>
      {q.error && <ErrorBox error={q.error} />}
      {q.isLoading && <Loading what="Reading Tally" />}
      {s && (
        <article className="panel p-6 print:border-0 print:p-0 print:shadow-none">
          <header className="mb-4 flex flex-wrap items-start justify-between gap-3 border-b border-seam pb-3">
            <div>
              <div className="text-[13px] text-steel">{company?.company_name}</div>
              <h2 className="display text-[22px] font-bold">{s.dealer}</h2>
              <div className="text-[13px] text-steel">Statement of account · {fmtD(s.from)} to {fmtD(s.to)}</div>
            </div>
            <div className="text-right">
              <div className="text-[13px] text-steel">Closing balance</div>
              <div className={`num display text-[26px] font-bold ${Number(s.closing) > 0 ? "text-bad" : "text-ok"}`}>{drcr(s.closing)}</div>
            </div>
          </header>
          {s.rows.length === 0 ? <Empty title="No entries in this period" hint={`Opening and closing balance ${drcr(s.opening)}`} /> : (
            <table className="tbl text-[13px]">
              <thead><tr><th>Date</th><th>Type</th><th>No.</th><th>Details</th><th className="text-right">Debit (₹)</th><th className="text-right">Credit (₹)</th><th className="text-right">Balance</th></tr></thead>
              <tbody>
                <tr className="font-semibold"><td>{fmtD(s.from)}</td><td colSpan={3}>Opening balance</td><td /><td /><td className="num text-right">{drcr(s.opening)}</td></tr>
                {s.rows.map((r, i) => (
                  <tr key={i}>
                    <td className="whitespace-nowrap">{fmtD(r.date)}</td><td>{r.type}</td><td className="num">{r.number}</td>
                    <td className="max-w-[260px] truncate text-steel" title={r.narration}>{r.reference || r.narration}</td>
                    <td className="num text-right">{amt(r.debit)}</td><td className="num text-right">{amt(r.credit)}</td>
                    <td className="num whitespace-nowrap text-right">{drcr(r.balance)}</td>
                  </tr>))}
                <tr className="border-t-2 border-ink/20 font-bold">
                  <td colSpan={4}>Total · closing balance</td><td className="num text-right">{amt(s.total_debit)}</td>
                  <td className="num text-right">{amt(s.total_credit)}</td><td className="num text-right">{drcr(s.closing)}</td>
                </tr>
              </tbody>
            </table>)}
          <p className="mt-4 text-[11.5px] text-steel">Dr = amount due from you. Cr = advance with us. Taken from our books in Tally on {new Date().toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}.</p>
        </article>
      )}
    </>
  );
}
