"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, call } from "@/lib/api";
import { useToast } from "@/components/toast";
import { Drawer, ErrorBox, Field, Loading, PageHeader, fmtQty } from "@/components/ui";
import { DOBadge, QtyChoice, ReasonDrawer, fmtDate, fmtTime, problemMessage, type DO } from "@/components/orders";

type Step = "load" | "dispatch" | "deliver" | "edit";

export default function DeliveryOrderPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const toast = useToast();
  const [step, setStep] = useState<Step | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const q = useQuery({ queryKey: ["do", id], queryFn: () => call(api.GET("/api/v1/delivery-orders/{do_id}", { params: { path: { do_id: id } } })) });
  const saved = (d: DO, msg: string) => {
    qc.setQueryData(["do", id], d);
    qc.invalidateQueries({ queryKey: ["dispatch"] });
    qc.invalidateQueries({ queryKey: ["vehicle-loads"] });
    qc.invalidateQueries({ queryKey: ["order", d.sales_order_id] });
    toast("ok", msg);
    setStep(null);
  };
  const cancel = useMutation({
    mutationFn: (reason: string) => call(api.POST("/api/v1/delivery-orders/{do_id}/cancel", { params: { path: { do_id: id } }, body: { reason } })),
    onSuccess: (d) => { saved(d, `${d.do_no} cancelled`); setCancelling(false); },
    onError: (e) => toast("bad", problemMessage(e)),
  });

  if (q.error) return <ErrorBox error={q.error} />;
  if (q.isLoading || !q.data) return <Loading />;
  const d = q.data;
  const can = (a: string) => d.can.includes(a);

  return (
    <>
      <div className="print:hidden">
        <PageHeader title={d.do_no} back={{ href: "/dispatch", label: "Dispatch" }}
                    sub={<><DOBadge status={d.status} /> <span className="ml-1">{d.dealer_name} · order <Link className="underline" href={`/orders/${d.sales_order_id}`}>{d.order_no}</Link></span></>}
                    actions={<div className="flex flex-wrap gap-2">
                      {can("load") && <button className="btn-quiet" onClick={() => setStep("load")}>Record loading</button>}
                      {can("dispatch") && <button className="btn-primary" onClick={() => setStep("dispatch")}>Send out (gate pass)</button>}
                      {can("deliver") && <button className="btn-primary" onClick={() => setStep("deliver")}>Record delivery</button>}
                      {can("edit") && <button className="btn-quiet" onClick={() => setStep("edit")}>Change truck / driver</button>}
                      {["LOADED", "DISPATCHED", "DELIVERED", "PARTIALLY_DELIVERED"].includes(d.status) && <button className="btn-quiet" onClick={() => window.print()}>Print gate pass</button>}
                      {can("cancel") && <button className="btn-danger" onClick={() => setCancelling(true)}>Cancel</button>}
                    </div>} />
      </div>

      <div className="grid gap-5 xl:grid-cols-[1fr_320px]">
        <section className="panel p-5 print:border-0 print:p-0">
          <div className="mb-4 hidden print:block">
            <div className="display text-[22px] font-bold">Gate pass · {d.do_no}</div>
            <div className="text-[13px]">Date {fmtDate(d.dispatch_date)} · Order {d.order_no}</div>
          </div>
          <dl className="grid gap-x-6 gap-y-2 text-[13.5px] sm:grid-cols-2">
            <KV k="Dealer" v={d.dealer_name} /><KV k="Site" v={d.site_name} />
            <KV k="Deliver to" v={d.delivery_address} /><KV k="From godown" v={d.godown_name} />
            <KV k="Truck" v={d.source === "DIRECT" ? `${d.vehicle_no} · direct from the factory`
                                   : `${d.vehicle_no} (${fmtQty(Number(d.vehicle_capacity_kg) / 1000, 1)} MT)`} />
            <KV k="Driver" v={d.driver_name ? `${d.driver_name} · ${d.driver_phone}` : null} />
            <KV k="Dispatch date" v={fmtDate(d.dispatch_date)} /><KV k="Planned weight" v={d.planned_weight_kg ? `${fmtQty(Number(d.planned_weight_kg) / 1000, 2)} MT` : "Unknown"} />
            {d.supplier_ref && <KV k="Factory / SAP ref" v={d.supplier_ref} />}
            {d.capacity_override_reason && <KV k="Loaded over capacity" v={d.capacity_override_reason} />}
          </dl>
          <table className="tbl mt-5">
            <thead><tr><th>Material</th><th className="text-right">Planned</th><th className="text-right">Loaded</th><th className="text-right">Sent</th><th className="text-right">Delivered</th><th className="text-right">Short / damaged</th></tr></thead>
            <tbody>{d.items.map((i) => (
              <tr key={i.id}>
                <td className="font-semibold">{i.product_name}</td>
                <td className="num text-right">{fmtQty(i.planned_qty)} {i.uom}</td>
                <td className="num text-right">{i.loaded_qty !== null ? fmtQty(i.loaded_qty) : "—"}</td>
                <td className="num text-right">{fmtQty(i.dispatched_qty)}</td>
                <td className="num text-right">{fmtQty(i.delivered_qty)}</td>
                <td className="num text-right">{Number(i.short_qty) + Number(i.damaged_qty) > 0 ? `${fmtQty(i.short_qty)} / ${fmtQty(i.damaged_qty)}` : "—"}</td>
              </tr>))}
            </tbody>
          </table>
          {d.remarks && <p className="mt-4 text-[13.5px]"><span className="text-steel">Remarks:</span> {d.remarks}</p>}
          {d.recipient_name && <p className="mt-2 text-[13.5px]"><span className="text-steel">Received by:</span> {d.recipient_name}{d.recipient_phone ? ` · ${d.recipient_phone}` : ""} · {fmtTime(d.delivered_at)}</p>}
          {d.delivery_remarks && <p className="mt-1 text-[13.5px] text-steel">{d.delivery_remarks}</p>}
          {d.cancel_reason && <p className="mt-2 text-[13.5px] text-bad">Cancelled: {d.cancel_reason}</p>}
          <div className="mt-16 hidden grid-cols-3 gap-8 text-center text-[12px] print:grid">
            <div className="border-t border-ink pt-1">Godown in-charge</div><div className="border-t border-ink pt-1">Driver</div><div className="border-t border-ink pt-1">Security</div>
          </div>
        </section>
        <aside className="panel h-fit p-4 print:hidden">
          <h2 className="display mb-2 text-[15px] font-bold">Timeline</h2>
          <ol className="space-y-2 text-[12.5px]">
            {d.events.map((e, i) => (
              <li key={i}><span className="font-semibold">{e.event.replaceAll("_", " ").toLowerCase()}</span>
                <span className="text-steel"> · {fmtTime(e.at)}{e.actor_name ? ` · ${e.actor_name}` : ""}</span>
                {e.notes && <div className="text-steel">{e.notes}</div>}</li>))}
          </ol>
        </aside>
      </div>

      {step && step !== "edit" && <QtyStep d={d} step={step} onClose={() => setStep(null)} onSaved={saved} />}
      {step === "edit" && <EditDO d={d} onClose={() => setStep(null)} onSaved={saved} />}
      <ReasonDrawer title="Cancel delivery order" label="Reason" open={cancelling} onClose={() => setCancelling(false)}
                    pending={cancel.isPending} onSubmit={(r) => cancel.mutate(r)} />
    </>
  );
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return <div><dt className="text-[12px] text-steel">{k}</dt><dd>{v || "—"}</dd></div>;
}

const TITLES = { load: "Record loading", dispatch: "Send out", deliver: "Record delivery" } as const;

/** Loading, gate-out and delivery all confirm quantities line by line; defaults are "as planned / as sent". */
function QtyStep({ d, step, onClose, onSaved }: { d: DO; step: "load" | "dispatch" | "deliver"; onClose: () => void; onSaved: (d: DO, msg: string) => void }) {
  const toast = useToast();
  const start = (i: DO["items"][number]) =>
    step === "deliver" ? i.dispatched_qty : step === "dispatch" ? (i.loaded_qty ?? i.planned_qty) : i.planned_qty;
  const [qty, setQty] = useState<Record<string, string>>({});
  const [bad, setBad] = useState<Record<string, { short: string; damaged: string }>>({});
  const [weight, setWeight] = useState<Record<string, string>>({});
  const [driverId, setDriverId] = useState(d.driver_id ?? "");
  const [recipient, setRecipient] = useState("");
  const [phone, setPhone] = useState("");
  const [remarks, setRemarks] = useState("");
  const [allowMore, setAllowMore] = useState(false);
  const [closeRest, setCloseRest] = useState(false);
  const n = (v: string | undefined | null) => Number(v || 0);
  // Deliver: whatever of the sent quantity is not delivered / short / damaged came back with the truck.
  const back = (i: DO["items"][number]) => n(i.dispatched_qty) - n(qty[i.id]) - n(bad[i.id]?.short) - n(bad[i.id]?.damaged);
  const more = step === "load" ? [] : d.items.flatMap((i) => {
    if (step === "deliver") {
      return back(i) < 0 ? [`${i.product_name}: ${fmtQty(-back(i))} ${i.uom} more than the ${fmtQty(i.dispatched_qty)} sent`] : [];
    }
    const over = Math.max(n(qty[i.id]) - n(String(start(i))), n(qty[i.id]) - n(i.order_open_qty));
    return over > 0 ? [`${i.product_name}: ${fmtQty(qty[i.id])} ${i.uom} is ${fmtQty(over)} more than ${
      n(qty[i.id]) - n(i.order_open_qty) >= over ? "left on the order" : "planned"}`] : [];
  });
  const less = step === "load" ? [] : d.items.flatMap((i) => {
    const rest = step === "deliver" ? n(i.order_open_qty) + Math.max(back(i), 0) : n(i.order_open_qty) - n(qty[i.id]);
    return rest > 0 ? [`${fmtQty(rest)} ${i.uom} ${i.product_name}`] : [];
  });
  useEffect(() => { setQty(Object.fromEntries(d.items.map((i) => [i.id, String(Number(start(i)))]))); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d.id, step]);
  const drivers = useQuery({ queryKey: ["drivers"], enabled: step === "dispatch",
    queryFn: () => call(api.GET("/api/v1/drivers", { params: { query: { page_size: 200 } } })) });

  const m = useMutation({
    mutationFn: () => {
      const path = { params: { path: { do_id: d.id } } };
      if (step === "deliver") {
        return call(api.POST("/api/v1/delivery-orders/{do_id}/deliver", { ...path, body: {
          recipient_name: recipient, recipient_phone: phone || null, remarks: remarks || null,
          allow_more: more.length > 0 && allowMore, close_rest: less.length > 0 && closeRest,
          lines: d.items.map((i) => ({ do_item_id: i.id, delivered_qty: qty[i.id] || "0",
                                       short_qty: bad[i.id]?.short || "0", damaged_qty: bad[i.id]?.damaged || "0",
                                       returned_qty: String(Math.max(back(i), 0)) })) } }));
      }
      const lines = d.items.map((i) => ({ do_item_id: i.id, qty: qty[i.id] || "0", actual_weight_kg: weight[i.id] || null }));
      if (step === "load") return call(api.POST("/api/v1/delivery-orders/{do_id}/load", { ...path, body: { lines, remarks: remarks || null } }));
      return call(api.POST("/api/v1/delivery-orders/{do_id}/dispatch", { ...path, body: { lines, driver_id: driverId || null, remarks: remarks || null,
        allow_more: more.length > 0 && allowMore, close_rest: less.length > 0 && closeRest } }));
    },
    onSuccess: (x) => onSaved(x, step === "deliver" ? "Delivery recorded" : step === "load" ? "Loading recorded" : `${x.vehicle_no} sent out`),
    onError: (e) => toast("bad", problemMessage(e)),
  });

  return (
    <Drawer title={`${TITLES[step]} · ${d.do_no}`} open onClose={onClose}>
      <div className="space-y-4">
        {d.items.map((i) => (
          <div key={i.id} className="rounded-[6px] border border-seam p-3">
            <div className="flex justify-between text-[13.5px]"><span className="font-semibold">{i.product_name}</span>
              <span className="num text-steel">{step === "deliver" ? "sent" : "planned"} {fmtQty(start(i))} {i.uom}</span></div>
            <div className="mt-2 grid grid-cols-2 gap-2">
              <Field label={step === "deliver" ? `Delivered (${i.uom})` : `Quantity (${i.uom})`}>
                <input className="input num" inputMode="decimal" value={qty[i.id] ?? ""} onChange={(e) => setQty({ ...qty, [i.id]: e.target.value })} />
              </Field>
              {step === "deliver" ? (
                <>
                  <Field label="Short"><input className="input num" inputMode="decimal" value={bad[i.id]?.short ?? ""}
                    onChange={(e) => setBad({ ...bad, [i.id]: { short: e.target.value, damaged: bad[i.id]?.damaged ?? "" } })} /></Field>
                  <Field label="Damaged"><input className="input num" inputMode="decimal" value={bad[i.id]?.damaged ?? ""}
                    onChange={(e) => setBad({ ...bad, [i.id]: { damaged: e.target.value, short: bad[i.id]?.short ?? "" } })} /></Field>
                </>
              ) : (
                <Field label="Weighbridge kg (optional)"><input className="input num" inputMode="decimal" value={weight[i.id] ?? ""}
                  onChange={(e) => setWeight({ ...weight, [i.id]: e.target.value })} /></Field>
              )}
            </div>
            {step === "deliver" && back(i) > 0 && (
              <p className="num mt-2 text-[12.5px] text-steel">Came back with the truck: <b className="text-ink">{fmtQty(back(i))} {i.uom}</b></p>)}
          </div>))}
        <QtyChoice more={more} less={less} allowMore={allowMore} setAllowMore={setAllowMore} closeRest={closeRest}
                   setCloseRest={setCloseRest} lessWord={step === "deliver" ? "is still to be sent" : "will be left"} />
        {step === "dispatch" && (
          <Field label="Driver">
            <select className="input" value={driverId} onChange={(e) => setDriverId(e.target.value)}>
              <option value="">Choose driver</option>
              {drivers.data?.items.filter((x) => x.is_active).map((x) => <option key={x.id} value={x.id}>{x.name} · {x.phone}</option>)}
            </select>
          </Field>
        )}
        {step === "deliver" && (
          <div className="grid grid-cols-2 gap-2">
            <Field label="Received by"><input className="input" value={recipient} onChange={(e) => setRecipient(e.target.value)} /></Field>
            <Field label="Their phone"><input className="input num" inputMode="tel" value={phone} onChange={(e) => setPhone(e.target.value)} /></Field>
          </div>
        )}
        <Field label="Remarks"><input className="input" value={remarks} onChange={(e) => setRemarks(e.target.value)} /></Field>
        <button className="btn-primary" disabled={m.isPending || (more.length > 0 && !allowMore) || (step === "deliver" && !recipient.trim()) || (step === "dispatch" && !driverId && d.source === "OWN")}
                onClick={() => m.mutate()}>{TITLES[step]}</button>
      </div>
    </Drawer>
  );
}

function EditDO({ d, onClose, onSaved }: { d: DO; onClose: () => void; onSaved: (d: DO, msg: string) => void }) {
  const toast = useToast();
  const [vehicleId, setVehicleId] = useState(d.vehicle_id ?? "");
  const [driverId, setDriverId] = useState(d.driver_id ?? "");
  const [override, setOverride] = useState("");
  const loads = useQuery({ queryKey: ["vehicle-loads"], queryFn: () => call(api.GET("/api/v1/dispatch/vehicles")) });
  const drivers = useQuery({ queryKey: ["drivers"], queryFn: () => call(api.GET("/api/v1/drivers", { params: { query: { page_size: 200 } } })) });
  const m = useMutation({
    mutationFn: () => call(api.PATCH("/api/v1/delivery-orders/{do_id}", { params: { path: { do_id: d.id } }, body: {
      version: d.version, vehicle_id: vehicleId, driver_id: driverId || null, capacity_override_reason: override || null } })),
    onSuccess: (x) => onSaved(x, "Saved"),
    onError: (e) => toast("bad", problemMessage(e)),
  });
  return (
    <Drawer title={`Change truck / driver · ${d.do_no}`} open onClose={onClose}>
      <div className="space-y-4">
        <Field label="Truck"><select className="input" value={vehicleId} onChange={(e) => setVehicleId(e.target.value)}>
          {loads.data?.map((v) => <option key={v.vehicle_id} value={v.vehicle_id}>{v.registration_no} · {fmtQty(Number(v.capacity_kg) / 1000, 1)} MT</option>)}
        </select></Field>
        <Field label="Driver"><select className="input" value={driverId} onChange={(e) => setDriverId(e.target.value)}>
          <option value="">Not set</option>
          {drivers.data?.items.filter((x) => x.is_active).map((x) => <option key={x.id} value={x.id}>{x.name} · {x.phone}</option>)}
        </select></Field>
        <Field label="Reason if over capacity (optional)"><input className="input" value={override} onChange={(e) => setOverride(e.target.value)} /></Field>
        <button className="btn-primary" disabled={m.isPending} onClick={() => m.mutate()}>Save</button>
      </div>
    </Drawer>
  );
}
