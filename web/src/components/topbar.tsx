"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, call } from "@/lib/api";
import { fmtMoney } from "@/components/ui";

const ago = (iso: string) => {
  const m = (Date.now() - new Date(iso).getTime()) / 6e4;
  return m < 1 ? "just now" : m < 60 ? `${Math.floor(m)} min ago` : m < 1440 ? `${Math.floor(m / 60)} h ago`
    : new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
};

type Hit = { href: string; title: string; sub: string; group: string };

/** One box for everything: order number, dealer name or mobile, truck number, delivery order, factory invoice.
 *  Press / or Ctrl+K anywhere to jump here. */
export function SearchBox() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLInputElement>(null);

  useEffect(() => { const t = setTimeout(() => setTerm(q.trim()), 250); return () => clearTimeout(t); }, [q]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes((e.target as HTMLElement)?.tagName);
      if ((e.key === "k" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !typing)) { e.preventDefault(); box.current?.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const res = useQuery({
    queryKey: ["search", term], enabled: term.length >= 2, staleTime: 15_000,
    queryFn: () => call(api.GET("/api/v1/search", { params: { query: { q: term } } })) as Promise<{
      orders: { id: string; order_no: string | null; dealer_name: string; status: string; total_amount: string; link: string }[];
      dealers: { id: string; name: string; balance: string | null; link: string }[];
      trucks: { id: string; do_no: string; vehicle: string; dealer_name: string; status: string; link: string }[];
      direct_trucks: { id: string; truck_no: string; invoice_no: string; status: string; link: string }[];
    }>,
  });
  const hits: Hit[] = res.data ? [
    ...res.data.orders.map((o) => ({ href: o.link, group: "Orders", title: o.order_no ?? "Draft", sub: `${o.dealer_name} · ${fmtMoney(o.total_amount)} · ${o.status.replace(/_/g, " ").toLowerCase()}` })),
    ...res.data.dealers.map((d) => ({ href: d.link, group: "Dealers", title: d.name, sub: d.balance !== null ? `Balance ${fmtMoney(d.balance)}` : "" })),
    ...res.data.trucks.map((t) => ({ href: t.link, group: "Trucks", title: `${t.vehicle} · ${t.do_no}`, sub: `${t.dealer_name} · ${t.status.replace(/_/g, " ").toLowerCase()}` })),
    ...res.data.direct_trucks.map((t) => ({ href: t.link, group: "Factory trucks", title: t.truck_no, sub: `Invoice ${t.invoice_no} · ${t.status === "OPEN" ? "loaded" : "done"}` })),
  ] : [];
  const go = (h: Hit) => { setOpen(false); setQ(""); router.push(h.href); };

  return (
    <div className="relative w-full max-w-[460px]">
      <input ref={box} type="search" className="input h-9 w-full pl-9" placeholder="Search orders, dealers, trucks…  ( / )"
             aria-label="Search everything" value={q} role="combobox" aria-expanded={open && term.length >= 2}
             aria-controls="search-results"
             onChange={(e) => { setQ(e.target.value); setOpen(true); setActive(0); }}
             onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
             onKeyDown={(e) => {
               if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, hits.length - 1)); }
               if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
               if (e.key === "Enter" && hits[active]) go(hits[active]);
               if (e.key === "Escape") { setOpen(false); box.current?.blur(); }
             }} />
      <svg aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-steel" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="8.5" cy="8.5" r="5.5" /><path d="m13 13 4 4" strokeLinecap="round" /></svg>
      {open && term.length >= 2 && (
        <ul id="search-results" role="listbox" className="absolute left-0 right-0 z-40 mt-1 max-h-[70vh] overflow-y-auto rounded-[8px] border border-seam bg-slab py-1 shadow-xl">
          {res.isLoading && <li className="px-4 py-3 text-[13px] text-steel">Searching…</li>}
          {res.data && hits.length === 0 && <li className="px-4 py-3 text-[13px] text-steel">Nothing found for “{term}”</li>}
          {hits.map((h, i) => (
            <li key={h.group + h.href + h.title} role="option" aria-selected={i === active}>
              {(i === 0 || hits[i - 1].group !== h.group) && <div className="px-4 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-steel">{h.group}</div>}
              <button onMouseDown={(e) => { e.preventDefault(); go(h); }} onMouseEnter={() => setActive(i)}
                      className={`block w-full px-4 py-1.5 text-left ${i === active ? "bg-blue-tint" : ""}`}>
                <div className="truncate text-[14px] font-semibold">{h.title}</div>
                {h.sub && <div className="truncate text-[12.5px] text-steel">{h.sub}</div>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

type Note = { id: string; kind: string; title: string; body: string | null; link: string | null; created_at: string; read_at: string | null };

/** The bell: orders waiting for you, your orders approved / held / delivered, sales rejected in Tally. */
export function NotificationBell() {
  const qc = useQueryClient();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ["notifications"], refetchInterval: 30_000,
    queryFn: () => call(api.GET("/api/v1/notifications", { params: { query: { limit: 30 } } })) as Promise<{ unread: number; items: Note[] }>,
  });
  const read = useMutation({
    mutationFn: (ids: string[] | null) => call(api.POST("/api/v1/notifications/read", { body: { ids } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
  const unread = q.data?.unread ?? 0;
  return (
    <div className="relative">
      <button className="relative flex h-9 w-9 items-center justify-center rounded-full hover:bg-concrete" aria-label={`Notifications${unread ? `, ${unread} new` : ""}`}
              aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <svg aria-hidden className="h-5 w-5 text-ink" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" /><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" /></svg>
        {unread > 0 && <span className="num absolute -right-0.5 -top-0.5 flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-bad px-1 text-[11px] font-bold text-white">{unread > 99 ? "99+" : unread}</span>}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-40 mt-2 w-[380px] max-w-[calc(100vw-24px)] rounded-[8px] border border-seam bg-slab shadow-xl">
            <div className="flex items-center justify-between border-b border-seam px-4 py-2.5">
              <span className="font-semibold">Notifications</span>
              {unread > 0 && <button className="text-[12.5px] text-blue underline" onClick={() => read.mutate(null)}>Mark all read</button>}
            </div>
            <ul className="max-h-[60vh] overflow-y-auto">
              {q.data?.items.length === 0 && <li className="px-4 py-8 text-center text-[13px] text-steel">Nothing yet</li>}
              {q.data?.items.map((n) => (
                <li key={n.id} className="border-b border-seam last:border-0">
                  <button className={`flex w-full gap-3 px-4 py-2.5 text-left hover:bg-concrete ${n.read_at ? "" : "bg-blue-tint/60"}`}
                          onClick={() => { if (!n.read_at) read.mutate([n.id]); setOpen(false); if (n.link) router.push(n.link); }}>
                    <span aria-hidden className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${n.read_at ? "bg-transparent" : "bg-blue"}`} />
                    <span className="min-w-0">
                      <span className="block text-[13.5px] font-semibold">{n.title}</span>
                      {n.body && <span className="block truncate text-[12.5px] text-steel">{n.body}</span>}
                      <span className="block text-[11.5px] text-steel">{ago(n.created_at)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <div className="border-t border-seam px-4 py-2 text-[12px] text-steel">The phone app shows these as notifications too.</div>
          </div>
        </>
      )}
    </div>
  );
}

