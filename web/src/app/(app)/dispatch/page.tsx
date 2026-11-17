"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Empty, ErrorBox, Loading, PageHeader, fmtQty } from "@/components/ui";
import { DOBadge, PlanDispatch, fmtDate } from "@/components/orders";
import type { components } from "@/api/schema";

type Pending = components["schemas"]["PendingLineOut"];
const TABS = [
  { key: "plan", label: "To plan" },
  { key: "yard", label: "At godown", status: ["PLANNED", "LOADED"] },
  { key: "road", label: "On the road", status: ["DISPATCHED"] },
  { key: "done", label: "Delivered", status: ["DELIVERED", "PARTIALLY_DELIVERED"] },
] as const;

export default function DispatchPage() {
  const { can } = useAuth();
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>(can("dispatch.plan") ? "plan" : "yard");
  const [planOrder, setPlanOrder] = useState<string | null>(null);
  const pending = useQuery({ queryKey: ["dispatch", "pending"], enabled: tab === "plan",
    queryFn: () => call(api.GET("/api/v1/dispatch/pending")) });
  const t = TABS.find((x) => x.key === tab)!;
  const dos = useQuery({ queryKey: ["dispatch", "dos", tab], enabled: tab !== "plan",
    queryFn: () => call(api.GET("/api/v1/delivery-orders", { params: { query: { page_size: 100, status: "status" in t ? [...t.status] : undefined } } })) });
  const loads = useQuery({ queryKey: ["vehicle-loads"], queryFn: () => call(api.GET("/api/v1/dispatch/vehicles")) });
  const order = useQuery({ queryKey: ["order", planOrder], enabled: !!planOrder,
    queryFn: () => call(api.GET("/api/v1/orders/{order_id}", { params: { path: { order_id: planOrder! } } })) });

  const grouped = useMemo(() => {
    const m = new Map<string, Pending[]>();
    for (const p of pending.data ?? []) m.set(p.order_id, [...(m.get(p.order_id) ?? []), p]);
    return [...m.values()];
  }, [pending.data]);

  return (
    <>
      <PageHeader title="Dispatch" sub="Plan trucks for approved orders, load them, send them out and record delivery." />
      {loads.data && loads.data.length > 0 && (
        <div className="mb-4 flex gap-2 overflow-x-auto pb-1">
          {loads.data.map((v) => {
            const pct = Math.min(100, (Number(v.planned_kg) / Number(v.capacity_kg)) * 100);
            return (
              <div key={v.vehicle_id} className="panel min-w-[150px] px-3 py-2">
                <div className="num text-[13px] font-semibold">{v.registration_no}</div>
                <div className="mt-1 h-1.5 rounded bg-concrete"><div className={`h-1.5 rounded ${pct > 100 ? "bg-bad" : "bg-blue"}`} style={{ width: `${pct}%` }} /></div>
                <div className="num mt-1 text-[11.5px] text-steel">
                  {v.on_road_dos > 0 ? "On the road" : v.status === "MAINTENANCE" ? "Maintenance" : Number(v.planned_kg) > 0
                    ? `${fmtQty(Number(v.planned_kg) / 1000, 1)} / ${fmtQty(Number(v.capacity_kg) / 1000, 1)} MT planned` : `Free · ${fmtQty(Number(v.capacity_kg) / 1000, 1)} MT`}
                </div>
              </div>);
          })}
        </div>
      )}
      <div role="tablist" className="mb-3 flex flex-wrap gap-1">
        {TABS.map((x) => (
          <button key={x.key} role="tab" aria-selected={tab === x.key} onClick={() => setTab(x.key)}
                  className={`h-8 rounded-[5px] px-3 text-[13px] font-semibold ${tab === x.key ? "bg-blue text-white" : "text-steel hover:bg-slab"}`}>{x.label}</button>))}
      </div>

      {tab === "plan" ? (
        <div className="panel overflow-x-auto">
          {pending.error ? <ErrorBox error={pending.error} /> : pending.isLoading ? <Loading /> : grouped.length === 0 ? (
            <Empty title="Nothing waiting for a truck" hint="Approved orders show up here." />
          ) : (
            <table className="tbl">
              <thead><tr><th>Order</th><th>Dealer</th><th>Material</th><th className="text-right">Left to send</th><th /></tr></thead>
              <tbody>{grouped.map((rows) => rows.map((p, i) => (
                <tr key={p.order_item_id}>
                  {i === 0 && <td rowSpan={rows.length} className="num align-top font-semibold">
                    <Link href={`/orders/${p.order_id}`} className="hover:underline">{p.order_no}</Link>
                    {p.priority === "URGENT" && <div className="text-[11px] font-bold text-bad">URGENT</div>}
                    <div className="text-[12px] font-normal text-steel">{fmtDate(p.order_date)}{p.requested_delivery_date ? ` · by ${fmtDate(p.requested_delivery_date)}` : ""}</div></td>}
                  {i === 0 && <td rowSpan={rows.length} className="align-top">{p.dealer_name}{p.site_name && <div className="text-[12px] text-steel">{p.site_name}</div>}</td>}
                  <td>{p.product_name}</td>
                  <td className="num text-right whitespace-nowrap">{fmtQty(p.remaining_qty)} {p.uom}
                    {Number(p.planned_qty) > 0 && <div className="text-[11.5px] text-warn">+{fmtQty(p.planned_qty)} on a truck</div>}</td>
                  {i === 0 && <td rowSpan={rows.length} className="align-top text-right">
                    {can("dispatch.plan") && <button className="btn-primary h-8" onClick={() => setPlanOrder(p.order_id)}>Plan truck</button>}</td>}
                </tr>)))}
              </tbody>
            </table>
          )}
        </div>
      ) : (
        <div className="panel overflow-x-auto">
          {dos.error ? <ErrorBox error={dos.error} /> : dos.isLoading ? <Loading /> : dos.data!.items.length === 0 ? <Empty title="Nothing here" /> : (
            <table className="tbl">
              <thead><tr><th>DO / gate pass</th><th>Date</th><th>Truck</th><th>Driver</th><th>Dealer</th><th>Material</th><th>Godown</th><th>Status</th></tr></thead>
              <tbody>{dos.data!.items.map((d) => (
                <tr key={d.id}>
                  <td className="num font-semibold"><Link href={`/dispatch/${d.id}`} className="hover:underline">{d.do_no}</Link></td>
                  <td className="num">{fmtDate(d.dispatch_date)}</td>
                  <td className="num">{d.vehicle_no}</td>
                  <td>{d.driver_name ?? <span className="text-warn">Not set</span>}</td>
                  <td>{d.dealer_name}</td>
                  <td className="max-w-[260px] truncate text-steel" title={d.summary}>{d.summary}</td>
                  <td className="text-steel">{d.godown_name}</td>
                  <td><DOBadge status={d.status} /></td>
                </tr>))}
              </tbody>
            </table>
          )}
        </div>
      )}
      {order.data && <PlanDispatch order={order.data} open={!!planOrder} onClose={() => setPlanOrder(null)} />}
    </>
  );
}
