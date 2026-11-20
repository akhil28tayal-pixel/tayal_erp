"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import { ApiError, api, call } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import { useToast } from "@/components/toast";
import { Badge, ErrorBox, Field, PageHeader, fmtMoney, fmtQty } from "@/components/ui";
import { problemMessage } from "@/components/orders";
import { NewDealerDrawer } from "@/components/new-dealer";
import { useAuth } from "@/lib/auth";

type Line = { key: number; product_id: string; qty: string; uom: string; rate: string };
let nextKey = 1;
const blankLine = (): Line => ({ key: nextKey++, product_id: "", qty: "", uom: "", rate: "" });

const FLAG_TEXT: Record<string, string> = {
  RATE_BELOW_MIN: "Below the dealer's allowed rate: needs admin approval",
  NO_DEALER_RATE: "No dealer rate on file: rate entered by hand, goes for approval",
};

export default function NewOrderPage() {
  return <Suspense><NewOrder /></Suspense>;
}

type LastOrder = { order_id: string; order_no: string | null; order_date: string; site_name: string | null;
  lines: { product_id: string; product_name: string; qty: string; uom: string; godown_name: string | null; rate: string }[] };

type WaMsg = { id: string; text: string; from_name: string | null; from_phone: string | null; received_at: string;
  client_ref: string; note: string | null; dealer_id: string | null;
  reading: { dealer_id: string | null; lines: { product_id: string | null; said: string; qty: string; unit: string | null; rate: string | null }[];
    delivery_date: string | null; urgent: boolean; site: string | null; notes: string | null; unclear: string[];
    dealer_choices: { id: string; name: string }[] } | null };

function NewOrder() {
  const router = useRouter();
  const toast = useToast();
  const params = useSearchParams();
  const draftId = params.get("draft");  // check and book a draft (e.g. one made from a WhatsApp message)
  const waId = params.get("wa");  // finish a WhatsApp message the reader was not sure about
  const [dealerQ, setDealerQ] = useState("");
  const [newDealer, setNewDealer] = useState(false);
  const { can } = useAuth();
  const [dealerId, setDealerId] = useState(params.get("dealer") ?? "");
  const [addressId, setAddressId] = useState("");
  const [site, setSite] = useState("");
  const [deliveryDate, setDeliveryDate] = useState("");
  const [urgent, setUrgent] = useState(false);
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState<Line[]>([blankLine()]);
  const dq = useDebounced(dealerQ);

  const dealers = useInfiniteQuery({
    queryKey: ["dealer-pick", dq], enabled: !dealerId, initialPageParam: 1,
    queryFn: ({ pageParam }) => call(api.GET("/api/v1/dealers", { params: { query: { q: dq || undefined, page: pageParam, page_size: 50 } } })),
    getNextPageParam: (last) => (last.page * last.page_size < last.total ? last.page + 1 : undefined),
  });
  const dealerRows = dealers.data?.pages.flatMap((p) => p.items) ?? [];
  const dealerTotal = dealers.data?.pages[0]?.total;
  const dealer = useQuery({ queryKey: ["dealer", dealerId], enabled: !!dealerId,
    queryFn: () => call(api.GET("/api/v1/dealers/{dealer_id}", { params: { path: { dealer_id: dealerId } } })) });
  // "Same as last time": the dealer's previous order, ready to book again
  const last = useQuery({ queryKey: ["last-order", dealerId], enabled: !!dealerId,
    queryFn: () => call(api.GET("/api/v1/orders/last", { params: { query: { dealer_id: dealerId } } })) as Promise<LastOrder | null> });
  const repeat = (o: LastOrder) => {
    setLines(o.lines.map((l) => ({ key: nextKey++, product_id: l.product_id, qty: String(Number(l.qty)), uom: l.uom, rate: "" })));
    if (o.site_name) setSite(o.site_name);
    toast("ok", "Filled from the last order. Check the quantity, then book.");
  };
  const draft = useQuery({ queryKey: ["order", draftId], enabled: !!draftId,
    queryFn: () => call(api.GET("/api/v1/orders/{order_id}", { params: { path: { order_id: draftId! } } })) });
  const wa = useQuery({ queryKey: ["wa-msg", waId], enabled: !!waId,
    queryFn: () => call(api.GET("/api/v1/whatsapp-orders/{msg_id}", { params: { path: { msg_id: waId! } } })) as Promise<WaMsg> });
  const filled = useRef(false);
  useEffect(() => {  // fill the form once from the draft or the message
    if (filled.current) return;
    const o = draft.data;
    if (o) {
      filled.current = true;
      setDealerId(o.dealer_id); setSite(o.site_name ?? ""); setDeliveryDate(o.requested_delivery_date ?? "");
      setUrgent(o.priority === "URGENT"); setNotes(o.special_instructions ?? ""); setAddressId(o.delivery_address_id ?? "");
      setLines(o.items.map((i) => ({ key: nextKey++, product_id: i.product_id, qty: String(Number(i.qty)), uom: i.uom,
        rate: i.rate_source === "MANUAL" ? String(Number(i.rate)) : "" })));
    }
    const m = wa.data;
    if (m?.reading) {
      filled.current = true;
      const r = m.reading;
      if (r.dealer_id) setDealerId(r.dealer_id);
      setSite(r.site ?? ""); setDeliveryDate(r.delivery_date ?? ""); setUrgent(!!r.urgent);
      setNotes([r.notes, `From WhatsApp${m.from_name ? ` (${m.from_name})` : ""}: “${m.text.slice(0, 300)}”`].filter(Boolean).join("\n"));
      const ok = r.lines.filter((l) => l.product_id);
      setLines(ok.length ? ok.map((l) => ({ key: nextKey++, product_id: l.product_id!, qty: Number(l.qty) ? String(Number(l.qty)) : "",
        uom: l.unit ?? "", rate: l.rate ? String(Number(l.rate)) : "" })) : [blankLine()]);
    }
  }, [draft.data, wa.data]);
  const fromWhatsApp = !!waId || draft.data?.source === "WHATSAPP";
  const products = useQuery({ queryKey: ["products-sellable"],
    queryFn: () => call(api.GET("/api/v1/products", { params: { query: { page_size: 200, erp_enabled: true } } })) });
  const byId = useMemo(() => Object.fromEntries((products.data?.items ?? []).map((p) => [p.id, p])), [products.data]);

  const ready = lines.filter((l) => l.product_id && Number(l.qty) > 0 && l.uom);
  const body = {
    dealer_id: dealerId, delivery_address_id: addressId || null, site_name: site || null,
    requested_delivery_date: deliveryDate || null, priority: (urgent ? "URGENT" : "NORMAL") as "URGENT" | "NORMAL",
    special_instructions: notes || null, source: (fromWhatsApp ? "WHATSAPP" : "OFFICE") as "WHATSAPP" | "OFFICE",
    // The godown is chosen when the truck is planned (Dispatch), not when booking.
    lines: ready.map((l) => ({ product_id: l.product_id, qty: l.qty, uom: l.uom, rate: l.rate || null })),
  };
  const debouncedBody = useDebounced(JSON.stringify(body), 400);
  const settled = JSON.parse(debouncedBody) as typeof body;  // what the user stopped typing, not the live state
  const preview = useQuery({
    queryKey: ["order-preview", debouncedBody], enabled: !!settled.dealer_id && settled.lines.length > 0, retry: false,
    queryFn: () => call(api.POST("/api/v1/orders/preview", { body: settled })),
  });

  const save = useMutation({
    mutationFn: async ({ submit, confirm }: { submit: boolean; confirm?: boolean }) => draft.data
      ? call(api.PUT("/api/v1/orders/{order_id}", { params: { path: { order_id: draft.data.id } },
          body: { ...body, version: draft.data.version, submit, confirm_duplicate: !!confirm } }))
      : call(api.POST("/api/v1/orders", { body: { ...body, submit, confirm_duplicate: !!confirm, client_ref: wa.data?.client_ref ?? null } })),
    onSuccess: (o) => {
      const msg = o.status === "DRAFT" ? "Draft saved" : o.status === "ON_HOLD" ? `${o.order_no} booked — on hold for admin`
        : o.status === "APPROVED" ? `${o.order_no} booked and approved` : `${o.order_no} booked — waiting approval`;
      toast(o.status === "ON_HOLD" ? "bad" : "ok", msg);
      router.push(`/orders/${o.id}`);
    },
    onError: (e, vars) => {
      if (e instanceof ApiError && e.problem.code === "DUPLICATE_ORDER") {
        if (window.confirm(`${e.problem.detail}\n\nBook it again as a separate order?`)) save.mutate({ ...vars, confirm: true });
        return;
      }
      toast("bad", problemMessage(e));
    },
  });

  const setLine = (key: number, patch: Partial<Line>) => setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const pickProduct = (key: number, id: string) => {
    const p = byId[id];
    const units = p?.units.filter((u) => u.is_sellable) ?? [];
    setLine(key, { product_id: id, uom: (units.find((u) => u.is_default_entry) ?? units[0])?.uom ?? p?.base_uom ?? "", rate: "" });
  };
  const priced = preview.data?.lines ?? [];
  const credit = preview.data?.credit;
  const d = dealer.data;

  return (
    <>
      <PageHeader title={fromWhatsApp ? "Check WhatsApp order" : draftId ? "Draft order" : "New order"}
                  back={fromWhatsApp ? { href: "/whatsapp-orders", label: "WhatsApp orders" } : { href: "/orders", label: "Orders" }}
                  sub={fromWhatsApp ? "Read from the dealer's message. Check the dealer, material and quantity, then book." : undefined} />
      {draft.data && draft.data.status !== "DRAFT" && (
        <div className="mb-4 rounded-[6px] border border-warn/30 bg-warn/5 px-4 py-3 text-[13.5px]">This order is already booked ({draft.data.order_no}).</div>)}
      {wa.data && <WaBubble m={wa.data} onDealer={(id) => { setDealerId(id); setAddressId(""); }} dealerChosen={!!dealerId} />}
      <div className="grid gap-5 xl:grid-cols-[1fr_340px]">
        <div className="space-y-5">
          <section className="panel p-4">
            <h2 className="display mb-3 text-[15px] font-bold">Dealer</h2>
            {!dealerId ? (
              <>
                <input className="input" autoFocus placeholder="Search dealer by name or code" aria-label="Search dealer"
                       value={dealerQ} onChange={(e) => setDealerQ(e.target.value)} />
                {dealerTotal !== undefined && <p className="mt-2 text-[12.5px] text-steel">{dq ? `${dealerTotal} found` : `${dealerTotal} dealers`}</p>}
                <ul className="mt-1 max-h-96 divide-y divide-seam/70 overflow-y-auto"
                    onScroll={(e) => { const el = e.currentTarget; if (el.scrollTop + el.clientHeight > el.scrollHeight - 200 && dealers.hasNextPage && !dealers.isFetchingNextPage) dealers.fetchNextPage(); }}>
                  {dealerRows.map((x) => {
                    const active = x.status === "ACTIVE";
                    return (
                      <li key={x.id}><button className="w-full px-2 py-2 text-left text-[13.5px] enabled:hover:bg-concrete/60 disabled:cursor-not-allowed disabled:text-steel"
                                             disabled={!active} onClick={() => { setDealerId(x.id); setAddressId(""); }}>
                        <span className="font-semibold">{x.name}</span> <span className="num text-steel">{x.code}</span>
                        {!active && <span className="ml-2 text-[12px] text-bad">{x.status.toLowerCase()}{x.blocked_reason ? ` · ${x.blocked_reason}` : ""} — cannot book</span>}
                      </button></li>);
                  })}
                  {dealers.hasNextPage && <li><button className="w-full px-2 py-2 text-[13px] text-steel underline" onClick={() => dealers.fetchNextPage()}>
                    {dealers.isFetchingNextPage ? "Loading…" : "Show more"}</button></li>}
                  {dealerTotal === 0 && <li className="px-2 py-3 text-[13px] text-steel">No dealer found. Sales executives see only their assigned dealers.</li>}
                </ul>
                {can("dealers.create") && (
                  <button className="btn-quiet mt-2 h-9" onClick={() => setNewDealer(true)}>+ New dealer (not in Tally yet)</button>)}
                <NewDealerDrawer open={newDealer} onClose={() => setNewDealer(false)} startName={dealerQ}
                                 onMade={(id) => { setDealerId(id); setAddressId(""); }} />
              </>
            ) : (
              <div className="space-y-3">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="font-semibold">{d?.name ?? "…"}</div>
                    <div className="num text-[12.5px] text-steel">{d?.code}{d?.tally_parent_group ? ` · ${d.tally_parent_group}` : ""}</div>
                    {d?.pending_in_tally && <div className="text-[12px] text-warn">New dealer — not in Tally yet (made at the next Tally sync)</div>}
                  </div>
                  <button className="btn-quiet h-8" onClick={() => { setDealerId(""); setDealerQ(""); }}>Change</button>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Deliver to">
                    <select className="input" value={addressId} onChange={(e) => setAddressId(e.target.value)}>
                      <option value="">Dealer&apos;s usual address</option>
                      {d?.addresses.filter((a) => a.is_active).map((a) => <option key={a.id} value={a.id}>{a.label}{a.area ? `, ${a.area}` : ""}</option>)}
                    </select>
                  </Field>
                  <Field label="Site / customer name (optional)"><input className="input" value={site} onChange={(e) => setSite(e.target.value)} /></Field>
                  <Field label="Needed by (optional)"><input type="date" className="input" value={deliveryDate} onChange={(e) => setDeliveryDate(e.target.value)} /></Field>
                  <label className="flex items-center gap-2 self-end pb-2 text-[13.5px]">
                    <input type="checkbox" checked={urgent} onChange={(e) => setUrgent(e.target.checked)} /> Urgent
                  </label>
                </div>
              </div>
            )}
          </section>

          {last.data && !fromWhatsApp && !draftId && (
            <section className="flex flex-wrap items-center justify-between gap-3 rounded-[10px] border-2 border-blue/30 bg-blue-tint px-4 py-3">
              <div className="min-w-0 text-[13.5px]">
                <div className="font-semibold">Last order {last.data.order_no} · {new Date(last.data.order_date + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" })}</div>
                <div className="truncate text-steel">{last.data.lines.map((l) => `${Number(l.qty)} ${l.uom.toLowerCase()} ${l.product_name}`).join(", ")}{last.data.site_name ? ` · ${last.data.site_name}` : ""}</div>
              </div>
              <button className="btn-primary h-10 px-5 text-[15px]" onClick={() => repeat(last.data!)}>Same as last time</button>
            </section>)}

          <section className="panel p-4">
            <h2 className="display mb-3 text-[15px] font-bold">Material</h2>
            <div className="space-y-3">
              {lines.map((l, idx) => {
                const p = byId[l.product_id];
                const pl = priced[ready.findIndex((r) => r.key === l.key)];
                return (
                  <div key={l.key} className="rounded-[6px] border border-seam p-3">
                    <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_110px_100px]">
                      <Field label={`Item ${idx + 1}`}>
                        <select className="input" value={l.product_id} onChange={(e) => pickProduct(l.key, e.target.value)}>
                          <option value="">Choose material</option>
                          {products.data?.items.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                        </select>
                      </Field>
                      <Field label="Quantity"><input className="input num" inputMode="decimal" value={l.qty} onChange={(e) => setLine(l.key, { qty: e.target.value })} /></Field>
                      <Field label="Unit">
                        <select className="input" value={l.uom} onChange={(e) => setLine(l.key, { uom: e.target.value })}>
                          {(p?.units.filter((u) => u.is_sellable) ?? []).map((u) => <option key={u.uom} value={u.uom}>{u.uom}</option>)}
                          {!p && <option value="">—</option>}
                        </select>
                      </Field>
                    </div>
                    <div className="mt-2 grid gap-2 sm:grid-cols-[150px_auto]">
                      <Field label={`Rate${pl ? ` / ${pl.rate_uom}` : ""}`}>
                        <input className="input num" inputMode="decimal" placeholder={pl?.list_rate ? String(Number(pl.list_rate)) : "Dealer rate"}
                               value={l.rate} onChange={(e) => setLine(l.key, { rate: e.target.value })} />
                      </Field>
                      <div className="flex items-end">
                        <button className="btn-quiet h-9" aria-label={`Remove item ${idx + 1}`} disabled={lines.length === 1}
                                onClick={() => setLines((ls) => ls.filter((x) => x.key !== l.key))}>Remove</button>
                      </div>
                    </div>
                    {pl && (
                      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px] text-steel">
                        <span className="num">{fmtQty(pl.rate_qty)} {pl.rate_uom} × ₹{fmtQty(pl.rate, 2)} = <b className="text-ink">{fmtMoney(pl.amount)}</b></span>
                        {pl.base_uom !== pl.uom && pl.base_uom !== "KG" && <span className="num">= {fmtQty(pl.base_qty)} {pl.base_uom}</span>}
                        {pl.weight_kg && <span className="num">{fmtQty(Number(pl.weight_kg) / 1000, 2)} MT</span>}
                        {pl.flags.map((f) => <Badge key={f} tone="warn">{FLAG_TEXT[f] ?? f}</Badge>)}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            <button className="btn-quiet mt-3" onClick={() => setLines((ls) => [...ls, blankLine()])}>Add another item</button>
          </section>

          <section className="panel p-4">
            <Field label="Instructions for dispatch (optional)"><textarea className="input h-20 py-2" value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
          </section>
        </div>

        <aside className="space-y-4 xl:sticky xl:top-4 xl:h-fit">
          <section className="panel p-4">
            <h2 className="display mb-3 text-[15px] font-bold">Summary</h2>
            {preview.error ? <ErrorBox error={preview.error} /> : (
              <dl className="space-y-1.5 text-[13.5px]">
                <div className="flex justify-between"><dt className="text-steel">Items</dt><dd className="num">{ready.length}</dd></div>
                <div className="flex justify-between"><dt className="text-steel">Weight</dt>
                  <dd className="num">{preview.data?.total_weight_kg ? `${fmtQty(Number(preview.data.total_weight_kg) / 1000, 2)} MT` : "—"}</dd></div>
                <div className="flex justify-between border-t border-seam pt-2 text-[16px] font-bold"><dt>Total</dt>
                  <dd className="num">{fmtMoney(preview.data?.total_amount)}</dd></div>
              </dl>
            )}
          </section>
          {credit && (
            <section className={`panel p-4 ${credit.result === "HOLD" ? "border-bad/50" : credit.result === "WARN" ? "border-warn/50" : ""}`}>
              <h2 className="display mb-2 flex items-center justify-between text-[15px] font-bold">Credit
                <Badge tone={credit.result === "PASS" ? "ok" : credit.result === "WARN" ? "warn" : "bad"}>
                  {credit.result === "PASS" ? "OK" : credit.result === "WARN" ? "Check" : "Will go on hold"}</Badge></h2>
              <dl className="space-y-1 text-[13px]">
                <div className="flex justify-between"><dt className="text-steel">Credit limit</dt><dd className="num">{credit.credit_limit ? fmtMoney(credit.credit_limit) : "Not set"}</dd></div>
                <div className="flex justify-between"><dt className="text-steel">Tally balance</dt><dd className="num">{fmtMoney(credit.tally_balance)}</dd></div>
                <div className="flex justify-between"><dt className="text-steel">Approved, not yet sent</dt><dd className="num">{fmtMoney(credit.open_order_exposure)}</dd></div>
                {credit.available_credit !== null && <div className="flex justify-between font-semibold"><dt>Available</dt><dd className="num">{fmtMoney(credit.available_credit)}</dd></div>}
              </dl>
              {credit.reasons.length > 0 && <ul className="mt-2 space-y-1 text-[12.5px] text-steel">{credit.reasons.map((r, i) => <li key={i}>{String(r.detail)}</li>)}</ul>}
            </section>
          )}
          <div className="flex flex-col gap-2">
            <button className="btn-primary justify-center" disabled={!dealerId || ready.length === 0 || save.isPending || !!preview.error}
                    onClick={() => save.mutate({ submit: true })}>Book order</button>
            <button className="btn-quiet justify-center" disabled={!dealerId || ready.length === 0 || save.isPending}
                    onClick={() => save.mutate({ submit: false })}>Save as draft</button>
          </div>
        </aside>
      </div>
    </>
  );
}

/** The dealer's message as it came in, with what the reader was unsure about. */
function WaBubble({ m, onDealer, dealerChosen }: { m: WaMsg; onDealer: (id: string) => void; dealerChosen: boolean }) {
  const r = m.reading;
  return (
    <section className="mb-5 grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="rounded-[12px] rounded-tl-none bg-[#DCF8C6] px-4 py-3 text-[15px] text-[#111B21] shadow-sm">
        <div className="mb-1 text-[12px] font-semibold text-[#075E54]">{m.from_name ?? "Dealer"}{m.from_phone ? ` · +${m.from_phone}` : ""}</div>
        <p className="whitespace-pre-wrap">{m.text}</p>
        <div className="mt-1 text-right text-[11px] text-[#667781]">{new Date(m.received_at).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</div>
      </div>
      <div className="panel px-4 py-3 text-[13.5px]">
        <div className="font-semibold">What the reader was not sure about</div>
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-warn">
          {m.note && <li>{m.note}</li>}
          {r?.unclear.map((u) => <li key={u}>{u}</li>)}
          {r?.lines.filter((l) => !l.product_id).map((l) => <li key={l.said}>Item not matched: “{l.said}”</li>)}
          {!m.note && !r?.unclear.length && <li className="text-steel">Nothing — just check it.</li>}
        </ul>
        {!dealerChosen && r?.dealer_choices.length ? (
          <div className="mt-2">
            <div className="text-steel">Did they mean:</div>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {r.dealer_choices.map((d) => <button key={d.id} className="rounded-full border border-blue px-3 py-1 text-blue hover:bg-blue-tint" onClick={() => onDealer(d.id)}>{d.name}</button>)}
            </div>
          </div>) : null}
      </div>
    </section>
  );
}
