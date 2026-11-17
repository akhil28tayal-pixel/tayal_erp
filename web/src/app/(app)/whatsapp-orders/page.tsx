"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { Badge, Empty, ErrorBox, Loading, PageHeader } from "@/components/ui";
import { problemMessage } from "@/components/orders";

type Line = { product_id: string | null; product_name: string | null; said: string; qty: string; unit: string | null; rate: string | null };
type Msg = {
  id: string; source: string; from_phone: string | null; from_name: string | null; text: string; received_at: string;
  status: string; note: string | null; reader: string | null; dealer_name: string | null; order_id: string | null;
  order_no: string | null; order_status: string | null; client_ref: string;
  reading: { lines: Line[]; delivery_date: string | null; urgent: boolean; site: string | null; unclear: string[] } | null;
};
type List = { ai_ready: boolean; webhook_ready: boolean; counts: Record<string, number>; items: Msg[] };

const TABS = [
  { key: "OPEN", label: "To check", match: ["DRAFTED", "NEEDS_HELP", "FAILED"] },
  { key: "BOOKED", label: "Booked", match: ["BOOKED"] },
  { key: "NOT_ORDER", label: "Not orders", match: ["NOT_ORDER"] },
  { key: "IGNORED", label: "Set aside", match: ["IGNORED"] },
] as const;
const STATUS: Record<string, [string, "ok" | "warn" | "bad" | "neutral"]> = {
  DRAFTED: ["Draft ready — check & book", "ok"], NEEDS_HELP: ["Needs a look", "warn"], FAILED: ["Could not read", "bad"],
  BOOKED: ["Booked", "neutral"], NOT_ORDER: ["Not an order", "neutral"], IGNORED: ["Set aside", "neutral"], READING: ["Reading…", "neutral"],
};
const when = (iso: string) => new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/** Orders that come in on WhatsApp. Paste a message (or dealers send it to the business number) — the reader turns
 *  it into a draft order; staff check it and book it. */
export default function WhatsAppOrdersPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useAuth();
  const [tab, setTab] = useState<string>("OPEN");
  const [text, setText] = useState("");
  const [phone, setPhone] = useState("");
  const q = useQuery({ queryKey: ["wa-orders", "list"], refetchInterval: 30_000,
    queryFn: () => call(api.GET("/api/v1/whatsapp-orders")) as Promise<List> });
  const refresh = () => qc.invalidateQueries({ queryKey: ["wa-orders"] });
  const read = useMutation({
    mutationFn: () => call(api.POST("/api/v1/whatsapp-orders/read", { body: { text, from_phone: phone || null } })) as Promise<Msg>,
    onSuccess: (m) => {
      setText(""); setPhone(""); refresh();
      if (m.status === "DRAFTED") toast("ok", `Draft made for ${m.dealer_name}. Check it and book.`);
      else if (m.status === "NOT_ORDER") toast("bad", "That does not look like an order.");
      else toast("bad", m.note ?? "Needs a look");
    },
    onError: (e) => toast("bad", problemMessage(e)),
  });
  const act = useMutation({
    mutationFn: ({ id, a }: { id: string; a: "ignore" | "reread" }) => a === "ignore"
      ? call(api.POST("/api/v1/whatsapp-orders/{msg_id}/ignore", { params: { path: { msg_id: id } } }))
      : call(api.POST("/api/v1/whatsapp-orders/{msg_id}/reread", { params: { path: { msg_id: id } } })),
    onSuccess: refresh,
    onError: (e) => toast("bad", problemMessage(e)),
  });

  const d = q.data;
  const t = TABS.find((x) => x.key === tab)!;
  const items = d?.items.filter((m) => (t.match as readonly string[]).includes(m.status)) ?? [];
  const count = (k: (typeof TABS)[number]) => k.match.reduce((n, s) => n + (d?.counts[s] ?? 0), 0);

  return (
    <>
      <PageHeader title="WhatsApp orders" sub="Dealers' order messages, read into draft orders for you to check and book" />

      {can("orders.create") && (
        <section className="panel mb-5 p-4">
          <form onSubmit={(e) => { e.preventDefault(); if (text.trim().length > 1) read.mutate(); }}>
            <label htmlFor="wa-text" className="display mb-2 block text-[16px] font-bold">Paste a dealer&apos;s message</label>
            <textarea id="wa-text" className="input h-28 py-2 text-[15px]" placeholder={"e.g.  Sharma ji 200 bag ambuja plus kal subah Rohini sec 7 pe bhejna"}
                      value={text} onChange={(e) => setText(e.target.value)} />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <input className="input num h-10 w-60" inputMode="tel" placeholder="Dealer's mobile (optional)" aria-label="Dealer's mobile"
                     value={phone} onChange={(e) => setPhone(e.target.value)} />
              <button className="btn-primary h-10 px-6 text-[15px]" disabled={read.isPending || text.trim().length < 2}>
                {read.isPending ? "Reading…" : "Read and make draft"}</button>
              <span className="text-[12.5px] text-steel">In WhatsApp: long-press the message → Copy, then paste here. The mobile number helps find the dealer.</span>
            </div>
          </form>
          {d && !d.ai_ready && (
            <p className="mt-3 rounded-[6px] bg-warn/10 px-3 py-2 text-[13px] text-warn">
              Using the simple built-in reader. For Hinglish, typos and voice-typed messages add ERP_ANTHROPIC_API_KEY to the .env file and restart.</p>)}
        </section>)}

      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1" role="tablist">
          {TABS.map((x) => (
            <button key={x.key} role="tab" aria-selected={tab === x.key} onClick={() => setTab(x.key)}
                    className={`rounded-[4px] px-3 py-1.5 text-[13.5px] ${tab === x.key ? "bg-blue text-white" : "text-steel hover:bg-slab"}`}>
              {x.label} <span className="num opacity-70">{count(x)}</span></button>))}
        </div>
        {d && <span className="text-[12.5px] text-steel">{d.webhook_ready ? "Receiving messages sent to the business number" : "Messages to the business number: not connected yet (see Settings)"}</span>}
      </div>

      {q.error && <ErrorBox error={q.error} />}
      {q.isLoading && <Loading />}
      {d && items.length === 0 && <div className="panel"><Empty title={tab === "OPEN" ? "Nothing to check" : "None"} hint={tab === "OPEN" ? "New WhatsApp orders appear here." : undefined} /></div>}
      <div className="space-y-3">
        {items.map((m) => (
          <article key={m.id} className="panel grid gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
            <div>
              <div className="mb-1 flex flex-wrap items-center gap-2 text-[12.5px] text-steel">
                <span className="font-semibold text-ink">{m.from_name ?? (m.source === "PASTE" ? "Pasted" : "Dealer")}</span>
                {m.from_phone && <span className="num">+{m.from_phone}</span>}<span>· {when(m.received_at)}</span>
                {m.source === "WHATSAPP" && <Badge>WhatsApp</Badge>}
              </div>
              <div className="rounded-[12px] rounded-tl-none bg-[#DCF8C6] px-4 py-2.5 text-[15px] text-[#111B21]">
                <p className="whitespace-pre-wrap">{m.text}</p></div>
            </div>
            <div className="flex flex-col justify-between gap-3">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={STATUS[m.status]?.[1]}>{STATUS[m.status]?.[0] ?? m.status}</Badge>
                  {m.reader && <span className="text-[12px] text-steel">read by {m.reader === "AI" ? "AI" : "built-in reader"}</span>}
                </div>
                <div className="mt-2 text-[14px]">
                  <div><span className="text-steel">Dealer: </span><b>{m.dealer_name ?? <span className="text-warn">not sure</span>}</b></div>
                  {m.reading?.lines.length ? (
                    <ul className="mt-1 space-y-0.5">
                      {m.reading.lines.map((l, i) => (
                        <li key={i}>{l.product_name ? <><b className="num">{Number(l.qty) || "?"} {l.unit?.toLowerCase()}</b> {l.product_name}</>
                          : <span className="text-warn">“{l.said}” — not matched</span>}{l.rate ? <span className="text-steel"> @ ₹{Number(l.rate)}</span> : null}</li>))}
                    </ul>) : null}
                  {(m.reading?.delivery_date || m.reading?.urgent || m.reading?.site) && (
                    <div className="mt-1 text-[13px] text-steel">
                      {m.reading?.urgent && <b className="text-bad">URGENT </b>}
                      {m.reading?.delivery_date && <>Needed by {new Date(m.reading.delivery_date + "T00:00:00").toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" })} </>}
                      {m.reading?.site && <>· Site: {m.reading.site}</>}
                    </div>)}
                  {m.note && <div className="mt-1 text-[13px] text-warn">{m.note}</div>}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {m.status === "DRAFTED" && m.order_id && <Link className="btn-primary" href={`/orders/new?draft=${m.order_id}`}>Check &amp; book</Link>}
                {m.status === "NEEDS_HELP" && can("orders.create") && <Link className="btn-primary" href={`/orders/new?wa=${m.id}`}>Finish the order</Link>}
                {m.status === "BOOKED" && m.order_id && <Link className="btn-quiet" href={`/orders/${m.order_id}`}>Open {m.order_no}</Link>}
                {(m.status === "FAILED" || m.status === "NEEDS_HELP" || m.status === "NOT_ORDER") && can("orders.create") &&
                  <button className="btn-quiet" disabled={act.isPending} onClick={() => act.mutate({ id: m.id, a: "reread" })}>Read again</button>}
                {["DRAFTED", "NEEDS_HELP", "FAILED", "NOT_ORDER"].includes(m.status) && can("orders.create") &&
                  <button className="text-[13px] text-steel underline" disabled={act.isPending} onClick={() => act.mutate({ id: m.id, a: "ignore" })}>Set aside</button>}
              </div>
            </div>
          </article>))}
      </div>
    </>
  );
}
