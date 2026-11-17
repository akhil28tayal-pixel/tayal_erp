"use client";

import Link from "next/link";
import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api, call } from "@/lib/api";
import { Badge, Empty, ErrorBox, Loading, PageHeader, Pager, fmtMoney } from "@/components/ui";
import { useDebounced } from "@/lib/hooks";

const STATUS_TONE = { ACTIVE: "ok", BLOCKED: "bad", INACTIVE: "neutral" } as const;

export default function DealersPage() {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const dq = useDebounced(q);
  const list = useQuery({
    queryKey: ["dealers", page, dq, status],
    queryFn: () => call(api.GET("/api/v1/dealers", {
      params: { query: { page, page_size: 50, q: dq || undefined, status: status || undefined } },
    })),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHeader title="Dealers" sub="Customers come from Tally (Sundry Debtors). Here you set sites, contacts, credit and rates." />
      <div className="mb-3 flex flex-wrap gap-2">
        <input className="input max-w-xs" placeholder="Search name, code or Tally ledger" aria-label="Search dealers"
               value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        <select className="input w-40" aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">All statuses</option><option value="ACTIVE">Active</option>
          <option value="BLOCKED">Blocked</option><option value="INACTIVE">Inactive</option>
        </select>
      </div>
      {list.error ? <ErrorBox error={list.error} /> : (
        <div className="panel overflow-x-auto">
          {list.isLoading ? <Loading /> : list.data!.items.length === 0 ? (
            <Empty title={dq || status ? "No dealers match these filters" : "No dealers yet"}
                   hint={dq || status ? "Clear the search or status filter." : "Run the Tally sync to bring in your Sundry Debtors ledgers."} />
          ) : (
            <table className="tbl">
              <thead><tr>
                <th>Dealer</th><th>Tally group</th><th className="text-right">Credit limit</th>
                <th className="text-right">Terms</th><th>Status</th>
              </tr></thead>
              <tbody>
                {list.data!.items.map((d) => (
                  <tr key={d.id}>
                    <td>
                      <Link href={`/dealers/${d.id}`} className="font-semibold hover:underline">{d.name}</Link>
                      <div className="text-[12px] text-steel">{d.code}{d.tally_ledger_name && d.tally_ledger_name !== d.name ? ` — Tally: ${d.tally_ledger_name}` : ""}</div>
                    </td>
                    <td className="text-steel">{d.tally_parent_group ?? "—"}</td>
                    <td className="num text-right">{d.credit_limit ? fmtMoney(d.credit_limit) : <span className="text-warn">Not set</span>}</td>
                    <td className="num text-right">{d.payment_terms_days != null ? `${d.payment_terms_days} days` : "—"}</td>
                    <td><Badge tone={STATUS_TONE[d.status as keyof typeof STATUS_TONE] ?? "neutral"}>{d.status.toLowerCase()}</Badge>
                      {d.tally_deleted && <span className="ml-2 text-[12px] text-bad">deleted in Tally</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {list.data && <Pager page={page} pageSize={50} total={list.data.total} onPage={setPage} />}
        </div>
      )}
    </>
  );
}
