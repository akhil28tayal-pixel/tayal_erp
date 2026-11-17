"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, call } from "@/lib/api";
import { useToast } from "@/components/toast";
import { Badge, Drawer, ErrorBox, Field, Loading, PageHeader, fmtMoney, fmtQty } from "@/components/ui";
import { DOBadge, OrderBadge, PlanDispatch, ReasonDrawer, fmtDate, fmtTime, problemMessage, type Order } from "@/components/orders";

type Cmd = "hold" | "release" | "reject" | "cancel" | "close";
const CMD: Record<Cmd, { title: string; label: string }> = {
  hold: { title: "Put on hold", label: "Why is it on hold?" },
  release: { title: "Release hold", label: "What changed?" },
  reject: { title: "Reject order", label: "Reason (the person who booked it will see this)" },
  cancel: { title: "Cancel order", label: "Reason" },
  close: { title: "Close order", label: "Why close it? Anything not yet sent is dropped." },
};
export default function OrderPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const toast = useToast();
  const [cmd, setCmd] = useState<Cmd | null>(null);
  const [approving, setApproving] = useState(false);
  const [planning, setPlanning] = useState(false);
  const order = useQuery({ queryKey: ["order", id], queryFn: () => call(api.GET("/api/v1/orders/{order_id}", { params: { path: { order_id: id } } })) });

  const done = (o: Order, msg: string) => { qc.setQueryData(["order", id], o); qc.invalidateQueries({ queryKey: ["orders"] }); toast("ok", msg); };
  const run = useMutation({
    mutationFn: ({ c, reason }: { c: Cmd; reason: string }) =>
      call(api.POST(`/api/v1/orders/{order_id}/${c}` as "/api/v1/orders/{order_id}/hold", { params: { path: { order_id: id } }, body: { reason } })),
    onSuccess: (o, v) => { done(o, `${CMD[v.c].title} done`); setCmd(null); },
    onError: (e) => toast("bad", problemMessage(e)),
  });
  const submit = useMutation({
    mutationFn: (confirm: boolean) => call(api.POST("/api/v1/orders/{order_id}/submit", { params: { path: { order_id: id } }, body: { confirm_duplicate: confirm } })),
    onSuccess: (o) => done(o, `${o.order_no} booked`),
    onError: (e) => {
      if (e instanceof ApiError && e.problem.code === "DUPLICATE_ORDER" && window.confirm(`${e.problem.detail}\n\nBook it anyway?`)) submit.mutate(true);
      else toast("bad", problemMessage(e));
    },
  });

  if (order.error) return <ErrorBox error={order.error} />;
  if (order.isLoading || !order.data) return <Loading />;
  const o = order.data;
  const can = (a: string) => o.can.includes(a);
  const lastCheck = o.credit_checks[o.credit_checks.length - 1];

  return (
    <>
      <PageHeader title={o.order_no ?? "Draft order"} back={{ href: "/orders", label: "Orders" }}
                  sub={<><OrderBadge status={o.status} hold={o.hold_kind} /> <span className="ml-1">{o.dealer_name} · {fmtDate(o.order_date)}{o.priority === "URGENT" ? " · URGENT" : ""}</span>
                    {o.tally_status && <TallyBadge status={o.tally_status} note={o.tally_note} />}</>}
                  actions={<div className="flex flex-wrap gap-2">
                    {can("submit") && <Link className="btn-quiet" href={`/orders/new?draft=${o.id}`}>Check &amp; change</Link>}
                    {can("submit") && <button className="btn-primary" disabled={submit.isPending} onClick={() => submit.mutate(false)}>Book order</button>}
                    {can("approve") && <button className="btn-primary" onClick={() => setApproving(true)}>Approve</button>}
                    {can("plan_dispatch") && o.items.some((i) => Number(i.remaining_qty) > 0) && <button className="btn-primary" onClick={() => setPlanning(true)}>Dispatch</button>}
                    {(["release", "hold", "reject", "close", "cancel"] as Cmd[]).filter(can).map((c) => (
                      <button key={c} className={c === "reject" || c === "cancel" ? "btn-danger" : "btn-quiet"} onClick={() => setCmd(c)}>{CMD[c].title}</button>
                    ))}
                  </div>} />

      {(o.status === "ON_HOLD" || o.review_reasons.length > 0) && !["APPROVED", "DELIVERED", "CLOSED", "CANCELLED", "REJECTED"].includes(o.status) && (
        <div className={`mb-4 rounded-[6px] border px-4 py-3 text-[13.5px] ${o.status === "ON_HOLD" ? "border-bad/30 bg-bad/5" : "border-warn/30 bg-warn/5"}`}>
          <p className="font-semibold">{o.status === "ON_HOLD" ? (o.hold_kind === "MANUAL" ? "On hold" : "On hold — needs an admin (Super Admin or Business Owner)") : "Needs approval because"}</p>
          <ul className="mt-1 list-disc pl-5">
            {o.status === "ON_HOLD" && o.hold_kind === "MANUAL" && <li>{o.hold_reason}</li>}
            {o.review_reasons.map((r, i) => <li key={i}>{String(r.detail)}</li>)}
          </ul>
        </div>
      )}
      {(can("approve") || can("reject")) && o.credit_now && (
        <div className="mb-4 max-w-xl"><DealerBalance c={o.credit_now} /></div>
      )}
      {o.cancel_reason && <p className="mb-4 text-[13.5px] text-steel">Reason: {o.cancel_reason}</p>}

      <div className="grid gap-5 xl:grid-cols-[1fr_340px]">
        <div className="space-y-5">
          <section className="panel overflow-x-auto">
            <table className="tbl">
              <thead><tr><th>#</th><th>Material</th><th className="text-right">Ordered</th><th className="text-right">Rate</th>
                <th className="text-right">Amount</th><th className="text-right">Sent</th><th className="text-right">Delivered</th><th className="text-right">Left</th></tr></thead>
              <tbody>{o.items.map((i) => (
                <tr key={i.id}>
                  <td className="num text-steel">{i.line_no}</td>
                  <td><span className="font-semibold">{i.product_name}</span>{i.remarks && <div className="text-[12px] text-steel">{i.remarks}</div>}</td>
                  <td className="num text-right whitespace-nowrap">{fmtQty(i.qty)} {i.uom}</td>
                  <td className="num text-right whitespace-nowrap">₹{fmtQty(i.rate, 2)}/{i.rate_uom}
                    {i.list_rate && Number(i.rate) !== Number(i.list_rate) && <div className="text-[11.5px] text-steel">dealer rate ₹{fmtQty(i.list_rate, 2)}</div>}</td>
                  <td className="num text-right">{fmtMoney(i.amount)}</td>
                  <td className="num text-right">{fmtQty(i.dispatched_qty)}</td>
                  <td className="num text-right">{fmtQty(i.delivered_qty)}</td>
                  <td className="num text-right">{Number(i.planned_qty) > 0 ? <span title="On a truck being loaded">{fmtQty(i.remaining_qty)} <span className="text-warn">(+{fmtQty(i.planned_qty)} planned)</span></span> : fmtQty(i.remaining_qty)}</td>
                </tr>))}
              </tbody>
              <tfoot><tr><td colSpan={4} className="px-3 py-2.5 text-right font-semibold">Total</td><td className="num px-3 py-2.5 text-right font-bold">{fmtMoney(o.total_amount)}</td><td colSpan={3} /></tr></tfoot>
            </table>
          </section>

          <section className="panel">
            <h2 className="display border-b border-seam px-4 py-3 text-[15px] font-bold">Trucks</h2>
            {o.delivery_orders.length === 0 ? <p className="px-4 py-4 text-[13.5px] text-steel">No truck planned yet.</p> : (
              <ul className="divide-y divide-seam/70">
                {o.delivery_orders.map((d) => (
                  <li key={d.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-[13.5px]">
                    <Link href={`/dispatch/${d.id}`} className="num font-semibold hover:underline">{d.do_no}</Link>
                    <span className="num">{d.vehicle_no}</span><span className="text-steel">{fmtDate(d.dispatch_date)}</span><DOBadge status={d.status} />
                  </li>))}
              </ul>
            )}
          </section>
        </div>

        <aside className="space-y-4">
          <section className="panel p-4 text-[13.5px]">
            <h2 className="display mb-1 text-[15px] font-bold">Deliver to</h2>
            <p>{o.delivery_location ?? <span className="text-warn">No address yet. Add one on the dealer page, or in the Tally ledger.</span>}</p>
            {o.delivery_location_source === "tally" && <p className="text-[12px] text-steel">Address from the Tally ledger</p>}
            {o.site_name && <p className="mt-1"><span className="text-steel">Site:</span> {o.site_name}</p>}
            {o.dealer_phone && <p className="num mt-1"><span className="text-steel">Dealer mobile:</span> <a className="hover:underline" href={`tel:+91${o.dealer_phone}`}>+91 {o.dealer_phone}</a></p>}
            {o.delivery_location && <a className="mt-1 inline-block text-[12.5px] underline" target="_blank" rel="noreferrer"
               href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(o.delivery_location.replace(/^[^:]*: /, ""))}`}>Open in Google Maps</a>}
          </section>
          <section className="panel p-4 text-[13.5px]">
            <dl className="space-y-1.5">
              <Row k="Booked by" v={o.booked_by_name} /><Row k="Sales executive" v={o.sales_exec_name} />
              <Row k="Needed by" v={o.requested_delivery_date ? fmtDate(o.requested_delivery_date) : null} />
              <Row k="Approved" v={o.approved_at ? `${fmtTime(o.approved_at)}${o.approved_by_name ? ` · ${o.approved_by_name}` : ""}` : null} />
            </dl>
            {o.special_instructions && <p className="mt-3 border-t border-seam pt-3 text-steel">{o.special_instructions}</p>}
          </section>
          {lastCheck && (
            <section className="panel p-4 text-[13px]">
              <h2 className="display mb-2 flex justify-between text-[15px] font-bold">Credit check
                <Badge tone={lastCheck.result === "PASS" ? "ok" : lastCheck.result === "WARN" ? "warn" : "bad"}>{lastCheck.result}</Badge></h2>
              <dl className="space-y-1">
                <Row k="Limit" v={lastCheck.credit_limit ? fmtMoney(lastCheck.credit_limit) : "Not set"} />
                <Row k="Tally balance" v={`${fmtMoney(lastCheck.tally_balance)}${lastCheck.balance_as_of ? ` (${fmtTime(lastCheck.balance_as_of)})` : ""}`} />
                <Row k="Other approved orders" v={fmtMoney(lastCheck.open_order_exposure)} />
                <Row k="Available" v={lastCheck.available_credit ? fmtMoney(lastCheck.available_credit) : "—"} />
              </dl>
            </section>
          )}
          <OrderHistory id={o.id} version={`${o.status}-${o.delivery_orders.map((d) => d.status).join()}-${o.tally_status ?? ""}`} />
        </aside>
      </div>

      {cmd && <ReasonDrawer open title={CMD[cmd].title} label={CMD[cmd].label} onClose={() => setCmd(null)} pending={run.isPending}
                            onSubmit={(reason) => run.mutate({ c: cmd, reason })} />}
      <Approve order={o} open={approving} onClose={() => setApproving(false)} onDone={(x) => {
        done(x, `${x.order_no} approved`);
        // Straight on to dispatch: the next thing that happens to an approved order.
        if (x.can.includes("plan_dispatch")) setPlanning(true);
      }} />
      <PlanDispatch order={o} open={planning} onClose={() => setPlanning(false)} />
    </>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-3"><dt className="text-steel">{k}</dt><dd className="num text-right">{v || "—"}</dd></div>;
}

function Approve({ order, open, onClose, onDone }: { order: Order; open: boolean; onClose: () => void; onDone: (o: Order) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const credit = order.status === "ON_HOLD" && order.hold_kind !== "MANUAL";
  const [reason, setReason] = useState("");
  const m = useMutation({
    mutationFn: () => call(api.POST("/api/v1/orders/{order_id}/approve", { params: { path: { order_id: order.id } },
      body: { reason: reason || null, override_credit: credit } })),
    onSuccess: (o) => { onDone(o); onClose(); },
    onError: (e) => { toast("bad", problemMessage(e)); qc.invalidateQueries({ queryKey: ["order", order.id] }); },
  });
  return (
    <Drawer title={`Approve ${order.order_no}`} open={open} onClose={onClose}>
      <div className="space-y-4 text-[13.5px]">
        <p>{order.dealer_name} · <b className="num">{fmtMoney(order.total_amount)}</b></p>
        {order.credit_now && <DealerBalance c={order.credit_now} />}
        {credit && <p className="rounded-[6px] border border-bad/30 bg-bad/5 px-3 py-2">This order failed the credit check. Approving it overrides the limit and is recorded against your name.</p>}
        <Field label={credit ? "Reason for the override" : "Note (optional)"}>
          <textarea className="input h-20 py-2" value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <button className="btn-primary" disabled={m.isPending || (credit && reason.trim().length < 3)} onClick={() => m.mutate()}>Approve order</button>
      </div>
    </Drawer>
  );
}

const TALLY: Record<string, [string, "ok" | "warn" | "bad" | "neutral"]> = {
  PENDING: ["Sending to Tally", "neutral"], WAITING_APPROVAL: ["Waiting for approval in Tally", "warn"],
  APPROVED: ["Approved in Tally", "ok"], REJECTED: ["Rejected in Tally", "bad"], FAILED: ["Not sent to Tally", "bad"],
};

function TallyBadge({ status, note }: { status: string; note?: string | null }) {
  const [label, tone] = TALLY[status] ?? [status, "neutral"];
  return <span className="ml-2" title={note ?? undefined}><Link href="/tally-sales"><Badge tone={tone}>{label}</Badge></Link></span>;
}

type CreditNow = NonNullable<Order["credit_now"]>;

/** The dealer's money position, in large type, for whoever approves the order. */
function DealerBalance({ c }: { c: CreditNow }) {
  const bal = Number(c.tally_balance ?? 0);
  const limit = c.credit_limit == null ? null : Number(c.credit_limit);
  const after = Number(c.total_after);
  const over = limit != null && after > limit;
  return (
    <div className={`rounded-[8px] border-2 p-4 ${over ? "border-bad bg-bad/5" : "border-seam bg-concrete/50"}`}>
      <div className="text-[13px] font-semibold text-steel">Dealer owes now (Tally)</div>
      <div className={`num display text-[34px] font-bold leading-tight ${bal > 0 ? "text-bad" : "text-ok"}`}>
        {c.tally_balance == null ? "Not known" : bal < 0 ? `${fmtMoney(-bal)} advance` : fmtMoney(bal)}
      </div>
      <div className={`text-[12.5px] ${c.is_stale ? "font-semibold text-warn" : "text-steel"}`}>
        {c.balance_as_of ? `As per Tally, ${fmtTime(c.balance_as_of)}` : "Never synced from Tally"}
        {c.is_stale && " — old, sync Tally before approving"}
      </div>
      <dl className="num mt-3 space-y-1 text-[14px]">
        <div className="flex justify-between"><dt className="text-steel">Other approved orders not yet sent</dt><dd>{fmtMoney(c.open_order_exposure)}</dd></div>
        <div className="flex justify-between"><dt className="text-steel">This order</dt><dd>{fmtMoney(c.this_order_amount)}</dd></div>
        <div className="flex justify-between border-t border-seam pt-1 font-bold"><dt>Total after this order</dt><dd>{fmtMoney(after)}</dd></div>
        <div className="flex justify-between"><dt className="text-steel">Credit limit</dt><dd>{limit == null ? "Not set" : fmtMoney(limit)}</dd></div>
      </dl>
      {over && <p className="mt-2 font-semibold text-bad">Over the limit by {fmtMoney(after - (limit ?? 0))}</p>}
    </div>
  );
}

type HistoryEntry = { at: string; who: string | null; what: string; detail?: string | null; kind: string };
const KIND_DOT: Record<string, string> = { order: "bg-blue", truck: "bg-[#0B7DD6]", tally: "bg-ok", whatsapp: "bg-[#25D366]" };

/** Everything that happened to the order, oldest first: booked, approved, trucks, Tally, WhatsApp. */
function OrderHistory({ id, version }: { id: string; version: string }) {
  const q = useQuery({
    queryKey: ["order-history", id, version],
    queryFn: () => call(api.GET("/api/v1/orders/{order_id}/history", { params: { path: { order_id: id } } })) as Promise<HistoryEntry[]>,
  });
  return (
    <section className="panel p-4">
      <h2 className="display mb-3 text-[15px] font-bold">Activity</h2>
      {q.isLoading && <p className="text-[12.5px] text-steel">Loading…</p>}
      <ol className="relative space-y-3 border-l-2 border-seam pl-4 text-[12.5px]">
        {q.data?.map((h, i) => (
          <li key={i} className="relative">
            <span aria-hidden className={`absolute -left-[22px] top-1 h-2.5 w-2.5 rounded-full ring-2 ring-slab ${KIND_DOT[h.kind] ?? "bg-steel"}`} />
            <div className="font-semibold text-ink">{h.what}</div>
            <div className="text-steel">{fmtTime(h.at)}{h.who ? ` · ${h.who}` : ""}</div>
            {h.detail && <div className="text-steel">{h.detail}</div>}
          </li>))}
      </ol>
    </section>
  );
}
