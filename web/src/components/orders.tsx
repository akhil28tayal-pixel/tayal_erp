"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, call } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import { useToast } from "@/components/toast";
import { useAuth } from "@/lib/auth";
import { Badge, Drawer, Field, fmtQty } from "@/components/ui";
import type { components } from "@/api/schema";

export type Order = components["schemas"]["OrderOut"];
export type DO = components["schemas"]["DOOut"];

type Tone = "ok" | "warn" | "bad" | "neutral";
export const ORDER_STATUS: Record<string, [string, Tone]> = {
  DRAFT: ["Draft", "neutral"],
  UNDER_REVIEW: ["Waiting approval", "warn"],
  ON_HOLD: ["On hold", "bad"],
  APPROVED: ["Approved", "ok"],
  PARTIALLY_DISPATCHED: ["Part dispatched", "ok"],
  DISPATCHED: ["Dispatched", "ok"],
  PARTIALLY_DELIVERED: ["Part delivered", "ok"],
  DELIVERED: ["Delivered", "ok"],
  CLOSED: ["Closed", "neutral"],
  REJECTED: ["Rejected", "bad"],
  CANCELLED: ["Cancelled", "neutral"],
};
export const DO_STATUS: Record<string, [string, Tone]> = {
  PLANNED: ["Planned", "warn"],
  LOADED: ["Loaded", "warn"],
  DISPATCHED: ["On the road", "ok"],
  DELIVERED: ["Delivered", "ok"],
  PARTIALLY_DELIVERED: ["Delivered with shortage", "bad"],
  CANCELLED: ["Cancelled", "neutral"],
};
export const HOLD_KIND: Record<string, string> = {
  CREDIT: "credit limit", STALE_BALANCE: "Tally balance not current", MANUAL: "put on hold",
};

export const SUPPLIER: Record<string, string> = { ambuja: "Ambuja", shree: "Shree", manual: "Factory truck" };

export function OrderBadge({ status, hold }: { status: string; hold?: string | null }) {
  const [label, tone] = ORDER_STATUS[status] ?? [status, "neutral"];
  return <Badge tone={tone}>{label}{status === "ON_HOLD" && hold ? ` · ${HOLD_KIND[hold] ?? hold}` : ""}</Badge>;
}

export function DOBadge({ status }: { status: string }) {
  const [label, tone] = DO_STATUS[status] ?? [status, "neutral"];
  return <Badge tone={tone}>{label}</Badge>;
}

export const fmtDate = (d: string | null | undefined) =>
  d ? new Date(d.length === 10 ? d + "T00:00:00" : d).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : "—";
export const fmtTime = (d: string | null | undefined) =>
  d ? new Date(d).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";

export function problemMessage(e: unknown) {
  return e instanceof ApiError ? e.problem.detail : "Something went wrong";
}

type Options = components["schemas"]["DispatchOptionsOut"];
type Source = { kind: "godown"; id: string } | { kind: "truck"; id: string } | null;

/** Refresh godown stock from Tally and wait for it (a few seconds to a minute). */
export function useStockRefresh(onDone: () => void) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      await call(api.POST("/api/v1/stock/refresh"));
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const s = await call(api.GET("/api/v1/stock"));
        if (s.latest && s.latest.status !== "RUNNING") {
          if (s.latest.status === "FAILED") toast("bad", s.latest.error ?? "Could not read stock from Tally");
          else toast("ok", "Stock updated from Tally");
          break;
        }
      }
    } catch (e) {
      toast("bad", problemMessage(e));
    } finally {
      setBusy(false);
      onDone();
    }
  };
  return { busy, run };
}

/** Send an approved order: pick where it goes from — a godown (with its Tally stock) or a factory truck in the
 *  Direct godown — then the quantities. Own trucks can be sent out in the same step; direct trucks always are. */
export function PlanDispatch({ order, open, onClose, onDone }: {
  order: Order; open: boolean; onClose: () => void; onDone?: (d: DO) => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useAuth();
  const canSend = can("dispatch.confirm");
  const lines = order.items.filter((i) => Number(i.remaining_qty) > 0);
  const [source, setSource] = useState<Source>(null);
  const [vehicleId, setVehicleId] = useState("");
  const [driverId, setDriverId] = useState("");
  const [sendNow, setSendNow] = useState(canSend);
  const [date, setDate] = useState(new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" }));
  const [remarks, setRemarks] = useState("");
  const [override, setOverride] = useState("");
  const [qty, setQty] = useState<Record<string, string>>({});
  const [factoryTruck, setFactoryTruck] = useState("");
  const truckQ = useDebounced(factoryTruck.trim(), 250);
  // Factory trucks used before: pick one instead of typing the whole number again.
  const knownTrucks = useQuery({ queryKey: ["factory-trucks", truckQ], enabled: open,
    queryFn: () => call(api.GET("/api/v1/dispatch/factory-truck-numbers", { params: { query: { q: truckQ || undefined } } })) as Promise<{ truck_no: string; last_used: string | null; times: number }[]> });
  const [allowMore, setAllowMore] = useState(false);
  const [closeRest, setCloseRest] = useState(false);
  const [factoryRef, setFactoryRef] = useState("");

  const options = useQuery({ queryKey: ["dispatch-options", order.id], enabled: open,
    queryFn: () => call(api.GET("/api/v1/orders/{order_id}/dispatch-options", { params: { path: { order_id: order.id } } })) });
  const loads = useQuery({ queryKey: ["vehicle-loads"], enabled: open,
    queryFn: () => call(api.GET("/api/v1/dispatch/vehicles")) });
  const drivers = useQuery({ queryKey: ["drivers"], enabled: open,
    queryFn: () => call(api.GET("/api/v1/drivers", { params: { query: { page_size: 200 } } })) });
  const refresh = useStockRefresh(() => options.refetch());

  const o: Options | undefined = options.data;
  const truck = source?.kind === "truck" ? o?.direct_trucks.find((t) => t.id === source.id) : undefined;
  const godown = source?.kind === "godown" ? o?.godowns.find((g) => g.godown_id === source.id) : undefined;
  // The Direct godown: material is on a factory truck. Listed trucks are picked below; otherwise type its number.
  const directGodown = godown?.kind === "DIRECT";
  const ownTruck = !!godown && !directGodown;

  // Start with the best source: a godown that has everything, else the first one with stock.
  useEffect(() => {
    if (!open || !o || source) return;
    const best = o.godowns.find((g) => g.enough) ?? o.godowns.find((g) => g.has_stock);
    if (best) setSource({ kind: "godown", id: best.godown_id });
    else if (o.direct_trucks.some((t) => t.matches)) setSource({ kind: "truck", id: o.direct_trucks.find((t) => t.matches)!.id });
  }, [open, o, source]);
  useEffect(() => { if (!open) { setSource(null); setAllowMore(false); setCloseRest(false); } }, [open]);

  // Default quantities: what is left on the order, capped by what is on a direct truck.
  useEffect(() => {
    if (!open) return;
    setQty(Object.fromEntries(lines.map((l) => {
      let q = Number(l.remaining_qty);
      if (truck) {
        const tl = truck.lines.find((x) => x.order_item_id === l.id);
        q = tl ? Math.min(q, Number(tl.left_qty ?? 0)) : 0;
      }
      return [l.id, String(q)];
    })));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, order.id, source?.id]);

  const vehicle = loads.data?.find((v) => v.vehicle_id === vehicleId);
  const picked = lines.filter((l) => Number(qty[l.id] || 0) > 0);
  const weightKnown = picked.every((l) => l.weight_per_unit_kg !== null);
  const loadKg = picked.reduce((a, l) => a + Number(qty[l.id] || 0) * Number(l.weight_per_unit_kg ?? 0), 0);
  const roomKg = vehicle ? Number(vehicle.capacity_kg) - Number(vehicle.planned_kg) : null;
  const shortAt = godown ? picked.filter((l) => {
    const gl = godown.lines.find((x) => x.order_item_id === l.id);
    return gl?.available_qty != null && Number(qty[l.id]) > Number(gl.available_qty);
  }) : [];

  const goesNow = !!truck || directGodown || (ownTruck && sendNow);
  const more = picked.filter((l) => Number(qty[l.id]) > Number(l.remaining_qty)).map((l) =>
    `${l.product_name}: ${fmtQty(qty[l.id])} ${l.uom} is ${fmtQty(Number(qty[l.id]) - Number(l.remaining_qty))} more than the ${fmtQty(l.remaining_qty)} left on the order`);
  const less = lines.filter((l) => Number(qty[l.id] || 0) < Number(l.remaining_qty)).map((l) =>
    `${fmtQty(Number(l.remaining_qty) - Number(qty[l.id] || 0))} ${l.uom} ${l.product_name}`);
  const save = useMutation({
    mutationFn: () => call(api.POST("/api/v1/delivery-orders", { body: {
      sales_order_id: order.id,
      godown_id: godown ? godown.godown_id : null,
      vehicle_id: ownTruck ? vehicleId : null,
      direct_truck_id: truck ? truck.id : null,
      direct_truck_no: directGodown ? factoryTruck.trim() : null,
      supplier_ref: directGodown ? factoryRef.trim() || null : null,
      send_now: ownTruck ? sendNow : false,
      driver_id: ownTruck ? driverId || null : null,
      dispatch_date: date, remarks: remarks || null, capacity_override_reason: override || null,
      lines: picked.map((l) => ({ order_item_id: l.id, qty: qty[l.id] })),
      allow_more: more.length > 0 && allowMore, close_rest: goesNow && less.length > 0 && closeRest,
    } })),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["order", order.id] });
      qc.invalidateQueries({ queryKey: ["dispatch"] });
      qc.invalidateQueries({ queryKey: ["vehicle-loads"] });
      qc.invalidateQueries({ queryKey: ["dispatch-options"] });
      qc.invalidateQueries({ queryKey: ["direct-trucks"] });
      toast("ok", d.status === "DISPATCHED" ? `${d.do_no} sent out on ${d.vehicle_no}. Record delivery when it reaches.`
                                            : `${d.do_no} planned on ${d.vehicle_no}`);
      onDone?.(d);
      onClose();
    },
    onError: (e) => toast("bad", problemMessage(e)),
  });
  const overCapacity = save.error instanceof ApiError && save.error.problem.code === "OVER_CAPACITY";
  const ready = !!source && picked.length > 0 && (more.length === 0 || allowMore) &&
    (truck || (directGodown ? factoryTruck.trim().length >= 4 : vehicleId && (!sendNow || driverId)));
  const action = truck ? (canSend ? `Send from ${truck.truck_no}` : "Plan from this truck")
                       : directGodown ? (factoryTruck.trim() ? `Send from ${factoryTruck.trim().toUpperCase()}` : "Send from the factory truck")
                       : sendNow ? "Send out now" : "Plan truck";

  return (
    <Drawer title={`Dispatch · ${order.order_no ?? ""}`} open={open} onClose={onClose}>
      <p className="mb-4 text-[13.5px] text-steel">{order.dealer_name}{order.site_name ? ` · ${order.site_name}` : ""}
        {" · "}{lines.map((l) => `${fmtQty(l.remaining_qty)} ${l.uom} ${l.product_name}`).join(", ")} to send</p>

      <div className="space-y-5">
        <section>
          <div className="mb-1.5 flex items-baseline justify-between gap-2">
            <h3 className="label !mb-0">Send from</h3>
            <span className="text-[12px] text-steel">
              {o?.stock_as_of ? `Stock as per Tally, ${fmtTime(o.stock_as_of)}` : "Stock not read from Tally yet"}
              {" · "}
              <button className="underline disabled:no-underline" disabled={refresh.busy || o?.stock_refreshing}
                      onClick={refresh.run}>{refresh.busy || o?.stock_refreshing ? "reading Tally…" : "refresh"}</button>
            </span>
          </div>
          {o?.stock_error && <p className="mb-2 text-[12.5px] text-bad">Last stock read failed: {o.stock_error}</p>}
          {options.isLoading && <p className="text-[13px] text-steel">Loading godowns…</p>}
          <div className="space-y-1.5" role="radiogroup" aria-label="Godown">
            {o?.godowns.map((g) => {
              const on = source?.kind === "godown" && source.id === g.godown_id;
              return (
                <button key={g.godown_id} role="radio" aria-checked={on}
                        onClick={() => setSource({ kind: "godown", id: g.godown_id })}
                        className={`w-full rounded-[6px] border px-3 py-2 text-left ${on ? "border-blue bg-blue-tint" : "border-seam hover:border-steel"}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold">{g.name}</span>
                    {g.kind === "DIRECT" ? <Badge tone="warn">Factory truck</Badge>
                      : g.enough ? <Badge tone="ok">Enough stock</Badge> : g.has_stock ? <Badge tone="warn">Part stock</Badge>
                      : <Badge tone="neutral">No stock</Badge>}
                  </div>
                  {g.kind === "DIRECT" ? (
                    <div className="mt-0.5 text-[12.5px] text-steel">Straight from the factory truck to the dealer</div>
                  ) : lines.map((l) => {
                    const gl = g.lines.find((x) => x.order_item_id === l.id);
                    return (
                      <div key={l.id} className="num mt-0.5 text-[12.5px] text-steel">
                        {lines.length > 1 && <>{l.product_name}: </>}
                        {gl?.available_qty == null ? "not in Tally stock"
                          : <><b className={Number(gl.available_qty) >= Number(l.remaining_qty) ? "text-ink" : "text-warn"}>
                              {fmtQty(gl.available_qty)} {l.uom}</b> available
                            {Number(gl.reserved_qty) > 0 && <> ({fmtQty(gl.stock_qty)} in stock, {fmtQty(gl.reserved_qty)} on trucks at the gate)</>}</>}
                      </div>);
                  })}
                </button>);
            })}
          </div>
          {o && o.direct_trucks.length > 0 && (
            <>
              <h3 className="label mt-4">Direct trucks · straight from the factory truck to the dealer</h3>
              <div className="space-y-1.5" role="radiogroup" aria-label="Direct truck">
                {o.direct_trucks.map((t) => {
                  const on = source?.kind === "truck" && source.id === t.id;
                  return (
                    <button key={t.id} role="radio" aria-checked={on} disabled={!t.matches}
                            onClick={() => setSource({ kind: "truck", id: t.id })}
                            className={`w-full rounded-[6px] border px-3 py-2 text-left disabled:opacity-50 ${on ? "border-blue bg-blue-tint" : "border-seam hover:border-steel"}`}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="num font-semibold">{t.truck_no}</span>
                        <span className="text-[12px] text-steel">{SUPPLIER[t.supplier] ?? t.supplier} · {fmtDate(t.invoice_date)}</span>
                      </div>
                      <div className="num mt-0.5 text-[12.5px] text-steel">
                        {t.lines.map((x) => `${fmtQty(x.left_qty ?? x.qty)} ${x.uom.toLowerCase()} ${x.tally_item}`).join(", ")} left
                        {" · "}Inv {t.invoice_no}{!t.matches && " · not what this order needs"}
                      </div>
                    </button>);
                })}
              </div>
            </>
          )}
        </section>

        {directGodown && (
          <div className="space-y-3 rounded-[6px] bg-concrete/60 px-3 py-3">
            <p className="text-[13px]">Direct: the material is on the factory truck and goes straight to {order.dealer_name}.
              {o && o.direct_trucks.some((t) => t.matches) ? " Pick it from the Direct trucks below, or type its number if it isn't listed." : ""}</p>
            <Field label="Factory truck number"><input className="input num uppercase" placeholder="HR55AB1234" value={factoryTruck} list="factory-trucks" autoComplete="off"
                   onChange={(e) => setFactoryTruck(e.target.value)} /></Field>
            <datalist id="factory-trucks">{(knownTrucks.data ?? []).map((t) => <option key={t.truck_no} value={t.truck_no} />)}</datalist>
            {(knownTrucks.data ?? []).length > 0 && (
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[12.5px]">
                <span className="text-steel">{factoryTruck.trim() ? "Matching" : "Used before"}:</span>
                {(knownTrucks.data ?? []).slice(0, 8).map((t) => (
                  <button key={t.truck_no} type="button" onClick={() => setFactoryTruck(t.truck_no)}
                          className={`num rounded-full border px-2.5 py-0.5 ${factoryTruck.trim().toUpperCase().replace(/\s/g, "") === t.truck_no ? "border-blue bg-blue text-white" : "border-seam hover:border-blue"}`}>
                    {t.truck_no}</button>))}
              </div>)}
            <Field label="Factory invoice / SAP reference (optional)"><input className="input" value={factoryRef}
                   onChange={(e) => setFactoryRef(e.target.value)} /></Field>
            <p className="text-[12.5px] text-steel">Sent out as soon as you save; record the delivery when it reaches.</p>
          </div>
        )}
        {ownTruck && (
          <>
            <Field label="Truck">
              <select className="input" value={vehicleId} onChange={(e) => setVehicleId(e.target.value)}>
                <option value="">Choose truck</option>
                {loads.data?.filter((v) => v.status !== "MAINTENANCE").map((v) => (
                  <option key={v.vehicle_id} value={v.vehicle_id}>
                    {v.registration_no} · {fmtQty(Number(v.capacity_kg) / 1000, 1)} MT
                    {Number(v.planned_kg) > 0 ? ` · ${fmtQty(Number(v.planned_kg) / 1000, 1)} MT already planned` : ""}
                    {v.on_road_dos > 0 ? " · on the road" : ""}
                  </option>))}
              </select>
            </Field>
            <Field label="Driver" hint={sendNow ? "Needed to send the truck out" : "Can be chosen later, but is needed before the truck leaves"}>
              <select className="input" value={driverId} onChange={(e) => setDriverId(e.target.value)}>
                <option value="">Choose driver</option>
                {drivers.data?.items.filter((d) => d.is_active).map((d) => <option key={d.id} value={d.id}>{d.name} · {d.phone}</option>)}
              </select>
            </Field>
            {canSend && (
              <label className="flex items-start gap-2 text-[13.5px]">
                <input type="checkbox" className="mt-1" checked={sendNow} onChange={(e) => setSendNow(e.target.checked)} />
                <span><b>Send out now</b> — the truck is loaded and leaving. Untick to plan it for loading later.</span>
              </label>
            )}
          </>
        )}
        {truck && (
          <p className="rounded-[6px] bg-concrete/60 px-3 py-2 text-[13px]">
            Truck <b className="num">{truck.truck_no}</b> goes from the factory straight to {order.dealer_name}. It is
            sent out as soon as you save{canSend ? "" : " (by someone who can send trucks out)"}; record the delivery when it reaches.
            Invoice {truck.invoice_no} is kept as the reference.
          </p>
        )}

        {source && (
          <fieldset>
            <legend className="label">Quantity</legend>
            <div className="space-y-2">
              {lines.map((l) => (
                <div key={l.id} className="flex items-center justify-between gap-2 rounded-[6px] border border-seam px-3 py-2">
                  <div className="min-w-0 text-[13.5px]">
                    <div className="truncate font-semibold">{l.product_name}</div>
                    <div className="num text-[12.5px] text-steel">{fmtQty(l.remaining_qty)} {l.uom} left on the order</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <input className="input num h-8 w-28" inputMode="decimal" aria-label={`${l.product_name} quantity`}
                           value={qty[l.id] ?? ""} onChange={(e) => setQty({ ...qty, [l.id]: e.target.value })} />
                    <span className="text-[13px] text-steel">{l.uom}</span>
                  </div>
                </div>))}
            </div>
            {shortAt.length > 0 && (
              <p className="mt-2 text-[12.5px] text-warn">More than Tally shows as available at {godown?.name}. Check the godown before sending.</p>
            )}
            <QtyChoice more={more} less={goesNow ? less : []} allowMore={allowMore} setAllowMore={setAllowMore}
                       closeRest={closeRest} setCloseRest={setCloseRest} />
          </fieldset>
        )}
        {ownTruck && (
          <p className={`num rounded-[6px] px-3 py-2 text-[13px] ${roomKg !== null && loadKg > roomKg ? "bg-bad/5 text-bad" : "bg-concrete/60"}`}>
            This load: <b>{weightKnown ? `${fmtQty(loadKg / 1000, 2)} MT` : "weight unknown for some items"}</b>
            {vehicle && <> · room on {vehicle.registration_no}: {fmtQty((roomKg ?? 0) / 1000, 2)} MT of {fmtQty(Number(vehicle.capacity_kg) / 1000, 1)} MT</>}
          </p>
        )}
        {source && (
          <details className="text-[13.5px]">
            <summary className="cursor-pointer text-steel">Date and remarks</summary>
            <div className="mt-3 space-y-3">
              <Field label="Dispatch date"><input type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
              <Field label="Remarks for the godown / driver"><input className="input" value={remarks} onChange={(e) => setRemarks(e.target.value)} /></Field>
            </div>
          </details>
        )}
        {overCapacity && (
          <Field label="Reason to load over capacity" hint="Only Operations and admins can do this">
            <input className="input" value={override} onChange={(e) => setOverride(e.target.value)} />
          </Field>
        )}
        <button className="btn-primary" disabled={!ready || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "Saving…" : action}
        </button>
      </div>
    </Drawer>
  );
}

/** When the quantity going out differs from the order: confirm sending more (the order is increased), and say
 *  whether the rest is still to be sent or the dealer doesn't want it (closed). */
export function QtyChoice({ more, less, allowMore, setAllowMore, closeRest, setCloseRest, lessWord = "left" }: {
  more: string[]; less: string[]; allowMore: boolean; setAllowMore: (v: boolean) => void;
  closeRest: boolean; setCloseRest: (v: boolean) => void; lessWord?: string;
}) {
  if (!more.length && !less.length) return null;
  return (
    <div className="mt-3 space-y-2">
      {more.length > 0 && (
        <label className={`flex items-start gap-2 rounded-[6px] px-3 py-2 text-[13px] ${allowMore ? "bg-blue-tint" : "bg-warn/10"}`}>
          <input type="checkbox" className="mt-1" checked={allowMore} onChange={(e) => setAllowMore(e.target.checked)} />
          <span>{more.map((m) => <span key={m} className="block">{m}.</span>)}
            <b>Yes, send more — increase the order</b> (same rate; the dealer is billed for what is delivered).</span>
        </label>)}
      {less.length > 0 && (
        <div className="rounded-[6px] bg-concrete/60 px-3 py-2 text-[13px]" role="radiogroup" aria-label="The rest of the order">
          <div className="mb-1">{less.join(", ")} of the order {lessWord}:</div>
          <label className="flex items-center gap-2"><input type="radio" checked={!closeRest} onChange={() => setCloseRest(false)} />
            Keep it — it will go on another truck</label>
          <label className="mt-1 flex items-center gap-2"><input type="radio" checked={closeRest} onChange={() => setCloseRest(true)} />
            Close it — the dealer doesn&apos;t want it</label>
        </div>)}
    </div>
  );
}

/** Reason prompt used by hold / reject / cancel / close. */
export function ReasonDrawer({ title, label, open, onClose, onSubmit, pending, extra }: {
  title: string; label: string; open: boolean; onClose: () => void; onSubmit: (reason: string) => void;
  pending?: boolean; extra?: React.ReactNode;
}) {
  const [reason, setReason] = useState("");
  useEffect(() => { if (open) setReason(""); }, [open]);
  return (
    <Drawer title={title} open={open} onClose={onClose}>
      <div className="space-y-4">
        {extra}
        <Field label={label}><textarea className="input h-24 py-2" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <button className="btn-primary" disabled={reason.trim().length < 3 || pending} onClick={() => onSubmit(reason.trim())}>{title}</button>
      </div>
    </Drawer>
  );
}

export function useSum(values: (string | number | null | undefined)[]) {
  return useMemo(() => values.reduce<number>((a, v) => a + Number(v ?? 0), 0), [values]);
}
