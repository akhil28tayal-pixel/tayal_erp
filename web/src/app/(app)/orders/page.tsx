"use client";

import Link from "next/link";
import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useDebounced } from "@/lib/hooks";
import { Empty, ErrorBox, Loading, PageHeader, Pager, fmtMoney } from "@/components/ui";
import { OrderBadge, fmtDate } from "@/components/orders";

const TABS: { key: string; label: string; status?: string[]; mine?: boolean }[] = [
  { key: "review", label: "Waiting approval", status: ["UNDER_REVIEW", "ON_HOLD"] },
  { key: "ready", label: "Ready to send", status: ["APPROVED", "PARTIALLY_DISPATCHED"] },
  { key: "road", label: "On the road", status: ["DISPATCHED", "PARTIALLY_DELIVERED"] },
  { key: "done", label: "Delivered", status: ["DELIVERED", "CLOSED"] },
  { key: "draft", label: "Drafts", status: ["DRAFT"] },
  { key: "all", label: "All" },
];

export default function OrdersPage() {
  return <Suspense><Orders /></Suspense>;
}

function Orders() {
  const { can } = useAuth();
  const router = useRouter();
  const asked = useSearchParams().get("tab");
  const salesOnly = !can("orders.approve");
  const wanted = asked === "open" ? "ready" : asked;  // old links
  const [tab, setTab] = useState(TABS.some((x) => x.key === wanted) ? wanted! : salesOnly ? "all" : "review");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const dq = useDebounced(q);
  const t = TABS.find((x) => x.key === tab)!;
  const orders = useQuery({
    queryKey: ["orders", tab, dq, page],
    queryFn: () => call(api.GET("/api/v1/orders", { params: { query: { page, page_size: 50, q: dq || undefined, status: t.status } } })),
  });

  return (
    <>
      <PageHeader title="Orders" sub={salesOnly ? "Orders you booked and orders of your dealers." : "Book, approve and follow orders to delivery."}
                  actions={can("orders.create") && <Link href="/orders/new" className="btn-primary">New order</Link>} />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div role="tablist" className="flex flex-wrap gap-1">
          {TABS.map((x) => (
            <button key={x.key} role="tab" aria-selected={tab === x.key} onClick={() => { setTab(x.key); setPage(1); }}
                    className={`h-8 rounded-[5px] px-3 text-[13px] font-semibold ${tab === x.key ? "bg-blue text-white" : "text-steel hover:bg-slab"}`}>
              {x.label}
            </button>))}
        </div>
        <input className="input ml-auto h-8 max-w-xs" placeholder="Dealer, order no. or site" aria-label="Search orders"
               value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
      </div>
      <div className="panel overflow-x-auto">
        {orders.error ? <ErrorBox error={orders.error} /> : orders.isLoading ? <Loading /> : orders.data!.items.length === 0 ? (
          <Empty title="No orders here" hint={can("orders.create") ? <Link className="underline" href="/orders/new">Book an order</Link> : undefined} />
        ) : (
          <>
            <table className="tbl">
              <thead><tr><th>Order</th><th>Date</th><th>Dealer</th><th>Material</th><th className="text-right">Amount</th><th>Status</th><th>Booked by</th></tr></thead>
              <tbody>{orders.data!.items.map((o) => (
                <tr key={o.id} className="cursor-pointer hover:bg-concrete/60" onClick={() => router.push(`/orders/${o.id}`)}>
                  <td className="num font-semibold"><Link href={`/orders/${o.id}`} className="hover:underline">{o.order_no ?? "Draft"}</Link>
                    {o.priority === "URGENT" && <span className="ml-1.5 text-[11px] font-bold text-bad">URGENT</span>}</td>
                  <td className="num whitespace-nowrap">{fmtDate(o.order_date)}</td>
                  <td>{o.dealer_name}</td>
                  <td className="max-w-[320px] truncate text-steel" title={o.summary}>{o.summary}</td>
                  <td className="num text-right">{fmtMoney(o.total_amount)}</td>
                  <td><OrderBadge status={o.status} hold={o.hold_kind} /></td>
                  <td className="text-steel">{o.booked_by_name ?? "—"}</td>
                </tr>))}
              </tbody>
            </table>
            <Pager page={page} pageSize={50} total={orders.data!.total} onPage={setPage} />
          </>
        )}
      </div>
    </>
  );
}
