"use client";

import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { Badge, Drawer, Empty, ErrorBox, Field, Loading, PageHeader, fmtMoney, fmtQty } from "@/components/ui";
import { fmtDate, problemMessage } from "@/components/orders";

/* The Purchase Uploader app, inside the ERP. Same flow: drop the Ambuja "Invoice details" / Shree "Invoice
   Summary Report" Excel files, check the rows, push the green ones to Tally. Direct-godown invoices also become
   Direct trucks that dispatch can deliver from. */

type Line = { description: string; item: string; mt: number; bags: number; amount: number; rate: number };
type Invoice = {
  id: string; invoice_no: string; date: string; supplier: "ambuja" | "shree"; truck: string; ref: string;
  source: string; godown: string; godown_overridden?: boolean; lines: Line[]; bags: number; mt: number;
  amount: number; status: "ready" | "problem" | "exists" | "posted"; status_note: string; problems: string[];
  direct: boolean; direct_truck: boolean; file: string; party_ledger: string;
};
type Unmapped = { kind: "ledger" | "product" | "godown"; supplier: "ambuja" | "shree"; text: string; label?: string;
  count: number; suggest?: string[]; options?: string[] };
type Payload = {
  invoices: Invoice[]; file_errors: string[]; files: { id: string; name: string; supplier: string }[];
  unmapped: Unmapped[]; tally_checked: boolean; tally_error: string; company: string; gst: boolean;
  options: { items: string[]; godowns: string[] };
  results?: { id: string; ok: boolean; error?: string; skipped?: boolean }[]; forgotten?: number;
};

const TABS = [
  { key: "all", label: "All" },
  { key: "ready", label: "Ready" },
  { key: "problem", label: "Needs fixing" },
  { key: "exists", label: "Already in Tally" },
  { key: "posted", label: "Pushed" },
] as const;
const STATUS: Record<Invoice["status"], [string, "ok" | "warn" | "bad" | "neutral"]> = {
  ready: ["Ready", "ok"], problem: ["Needs fixing", "bad"], exists: ["Already in Tally", "neutral"], posted: ["Pushed", "neutral"],
};
const SUP = { ambuja: "Ambuja", shree: "Shree" };
const label = (src: string) => src.replace(/^\d+\s*-\s*/, "");

export default function PurchasesPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useAuth();
  const [tab, setTab] = useState<string>("all");
  // Green rows are ticked by default (as in the app); this holds the ones someone unticked.
  const [skip, setSkip] = useState<Set<string>>(new Set());
  const [xml, setXml] = useState<{ id: string; xml: string } | null>(null);
  const [settings, setSettings] = useState(false);
  const [drag, setDrag] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const q = useQuery({ queryKey: ["purchases"], queryFn: () => call(api.GET("/api/v1/purchases", { params: { query: { check_tally: true } } })) as Promise<Payload> });
  const set = (p: Payload) => qc.setQueryData(["purchases"], p);

  const upload = useMutation({
    mutationFn: (files: File[]) => {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      return call(api.POST("/api/v1/purchases/upload", { body: fd as any })) as Promise<Payload>;
    },
    onSuccess: (p) => set(p),
    onError: (e) => toast("bad", problemMessage(e)),
  });
  const post = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) =>
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      call(api.POST(path as any, { body: (body ?? {}) as any })) as Promise<Payload>,
    onSuccess: (p) => {
      set(p);
      if (p.results) {
        const ok = p.results.filter((r) => r.ok).length, bad = p.results.filter((r) => !r.ok && !r.skipped);
        if (ok) toast("ok", `${ok} pushed to Tally${p.invoices.some((i) => i.direct && i.status === "posted") ? ". Direct invoices are now on Stock & trucks." : ""}`);
        if (bad.length) toast("bad", `${bad.length} not pushed: ${bad[0].error}`);
      }
      if (p.forgotten !== undefined) toast("ok", `${p.forgotten} forgotten — they can be pushed again`);
    },
    onError: (e) => toast("bad", problemMessage(e)),
  });
  const addTruck = useMutation({
    mutationFn: (id: string) => call(api.POST("/api/v1/purchases/direct-truck", { body: { id } })),
    onSuccess: (r) => { toast("ok", `Truck ${r.truck_no} added to Direct trucks`); qc.invalidateQueries({ queryKey: ["purchases"] }); },
    onError: (e) => toast("bad", problemMessage(e)),
  });
  const showXml = async (id: string) => {
    try { setXml({ id, xml: (await call(api.GET("/api/v1/purchases/xml", { params: { query: { id } } }))).xml as string }); }
    catch (e) { toast("bad", problemMessage(e)); }
  };

  const p = q.data;
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: p?.invoices.length ?? 0 };
    p?.invoices.forEach((i) => { c[i.status] = (c[i.status] ?? 0) + 1; });
    return c;
  }, [p]);
  const rows = (p?.invoices ?? []).filter((i) => tab === "all" || i.status === tab);
  const ready = (p?.invoices ?? []).filter((i) => i.status === "ready");
  const sel = ready.filter((i) => !skip.has(i.id));
  const selTotal = sel.reduce((a, i) => a + i.amount, 0);

  const onFiles = (list: FileList | null) => {
    const files = Array.from(list ?? []).filter((f) => /\.(xlsx?|xlsm)$/i.test(f.name));
    if (files.length) upload.mutate(files);
    else if (list?.length) toast("bad", "Choose the Excel files (.xlsx or .xls)");
  };

  return (
    <>
      <PageHeader title="Purchases" sub={<>Ambuja and Shree invoices into Tally{p?.company ? <> · <b>{p.company}</b></> : null}</>}
        actions={<>
          <button className="btn-quiet" disabled={post.isPending} onClick={() => post.mutate({ path: "/api/v1/purchases/refresh" })}>Re-check Tally</button>
          {(can("tally.map") || can("admin.settings")) && <button className="btn-quiet" onClick={() => setSettings(true)}>Settings</button>}
        </>} />

      <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
           onDrop={(e) => { e.preventDefault(); setDrag(false); onFiles(e.dataTransfer.files); }}
           className={`panel mb-4 flex flex-col items-center justify-center gap-2 border-dashed px-4 py-6 text-center ${drag ? "border-blue bg-blue-tint" : ""}`}>
        <p className="text-[14px]"><b>Drop the Excel files here</b> — Ambuja “Invoice details” and/or Shree “Invoice Summary Report”</p>
        <input ref={fileRef} type="file" multiple accept=".xlsx,.xls,.xlsm" className="hidden" onChange={(e) => { onFiles(e.target.files); e.target.value = ""; }} />
        <button className="btn-quiet h-8" disabled={upload.isPending} onClick={() => fileRef.current?.click()}>
          {upload.isPending ? "Reading and checking Tally…" : "Choose files"}
        </button>
        {!!p?.files.length && (
          <div className="mt-1 flex flex-wrap justify-center gap-1.5">
            {p.files.map((f) => (
              <span key={f.id} className="inline-flex items-center gap-1 rounded-[4px] bg-concrete px-2 py-0.5 text-[12.5px]">
                {f.name}{f.supplier && <span className="text-steel">· {SUP[f.supplier as "ambuja"] ?? f.supplier}</span>}
                <button aria-label={`Remove ${f.name}`} className="ml-1 text-steel hover:text-bad"
                        onClick={() => post.mutate({ path: `/api/v1/purchases/files/${f.id}/remove` })}>×</button>
              </span>))}
          </div>
        )}
      </div>

      {q.error && <ErrorBox error={q.error} />}
      {q.isLoading && <Loading what="Loading purchases" />}
      {p && (
        <>
          {(p.tally_error || p.file_errors.length > 0) && (
            <div className="mb-3 space-y-1 rounded-[6px] border border-bad/30 bg-bad/5 px-4 py-2.5 text-[13.5px] text-bad">
              {p.tally_error && <p><b>Tally:</b> {p.tally_error}</p>}
              {p.file_errors.map((e) => <p key={e}>{e}</p>)}
            </div>
          )}
          {p.unmapped.length > 0 && <Mapping items={p.unmapped} options={p.options} busy={post.isPending}
                                              onSave={(body) => post.mutate({ path: "/api/v1/purchases/map", body })} />}

          {p.invoices.length === 0 ? (
            <div className="panel"><Empty title="No invoices loaded" hint="Drop today's Ambuja and Shree files above." /></div>
          ) : (
            <div className="panel overflow-hidden">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-seam px-3 py-2">
                <div className="flex flex-wrap gap-1" role="tablist">
                  {TABS.map((t) => (
                    <button key={t.key} role="tab" aria-selected={tab === t.key} onClick={() => setTab(t.key)}
                            className={`rounded-[4px] px-2.5 py-1 text-[13px] ${tab === t.key ? "bg-blue text-white" : "text-steel hover:bg-concrete"}`}>
                      {t.label} <span className="num opacity-70">{counts[t.key] ?? 0}</span>
                    </button>))}
                </div>
                <div className="flex gap-1.5">
                  {counts.posted > 0 && <button className="btn-quiet h-8" onClick={() => {
                    if (window.confirm("Forget that these invoices were pushed, so they can be pushed again? Do this only after deleting them in Tally.")) post.mutate({ path: "/api/v1/purchases/forget", body: { ids: null } });
                  }}>Forget pushed</button>}
                  <button className="btn-quiet h-8" onClick={() => { if (window.confirm("Clear all files from the table?")) post.mutate({ path: "/api/v1/purchases/clear" }); }}>Clear all</button>
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-[13.5px]">
                  <thead className="bg-concrete/60 text-left text-[12px] text-steel">
                    <tr>
                      <th className="w-8 px-3 py-2"><input type="checkbox" aria-label="Tick all ready"
                        checked={ready.length > 0 && sel.length === ready.length}
                        onChange={(e) => setSkip(new Set(e.target.checked ? [] : ready.map((i) => i.id)))} /></th>
                      <th className="px-2 py-2">Invoice</th><th className="px-2 py-2">Truck</th><th className="px-2 py-2">Source → godown</th>
                      <th className="px-2 py-2">Item</th><th className="px-2 py-2 text-right">Bags</th>
                      <th className="px-2 py-2 text-right">Rate/bag</th><th className="px-2 py-2 text-right">Amount</th><th className="px-2 py-2">Status</th><th />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((i) => (
                      <tr key={i.id} className="border-t border-seam align-top">
                        <td className="px-3 py-2">{i.status === "ready" && <input type="checkbox" aria-label={`Push ${i.invoice_no}`}
                          checked={!skip.has(i.id)} onChange={(e) => { const n = new Set(skip); if (e.target.checked) n.delete(i.id); else n.add(i.id); setSkip(n); }} />}</td>
                        <td className="px-2 py-2"><div className="num font-semibold">{i.invoice_no}</div>
                          <div className="text-[12px] text-steel">{SUP[i.supplier]} · {fmtDate(i.date)}</div></td>
                        <td className="num px-2 py-2">{i.truck || "—"}</td>
                        <td className="px-2 py-2">
                          <div className="text-[12px] text-steel">{label(i.source)}</div>
                          {i.status === "ready" || i.status === "problem" ? (
                            <select className="input mt-0.5 h-7 w-40 px-1.5 text-[13px]" value={i.godown}
                                    aria-label={`Godown for ${i.invoice_no}`}
                                    onChange={(e) => post.mutate({ path: "/api/v1/purchases/override", body: { id: i.id, godown: e.target.value || null } })}>
                              {!i.godown && <option value="">Choose godown</option>}
                              {i.godown && !p.options.godowns.includes(i.godown) && <option value={i.godown}>{i.godown}</option>}
                              {p.options.godowns.map((g) => <option key={g} value={g}>{g}</option>)}
                            </select>
                          ) : <div className="font-semibold">{i.godown}</div>}
                          {i.direct && <div className="mt-0.5"><Badge tone="warn">Direct truck</Badge></div>}
                        </td>
                        <td className="px-2 py-2">{i.lines.map((l, n) => <div key={n}>{l.item || <span className="text-bad">{l.description}</span>}</div>)}</td>
                        <td className="num px-2 py-2 text-right">{i.lines.map((l, n) => <div key={n}>{fmtQty(l.bags)}</div>)}</td>
                        <td className="num px-2 py-2 text-right">{i.lines.map((l, n) => <div key={n}>{fmtQty(l.rate, 2)}</div>)}</td>
                        <td className="num px-2 py-2 text-right font-semibold">{fmtMoney(i.amount)}</td>
                        <td className="px-2 py-2"><Badge tone={STATUS[i.status][1]}>{STATUS[i.status][0]}</Badge>
                          {i.status !== "ready" && <div className="mt-0.5 max-w-[220px] text-[12px] text-steel">{i.status_note}</div>}
                          {i.problems.length > 1 && <div className="text-[12px] text-bad">+{i.problems.length - 1} more</div>}</td>
                        <td className="whitespace-nowrap px-2 py-2 text-right">
                          <button className="text-[12.5px] text-steel underline" onClick={() => showXml(i.id)}>XML</button>
                          {i.direct && !i.direct_truck && (i.status === "exists" || i.status === "posted") && (
                            <button className="ml-2 text-[12.5px] text-steel underline" disabled={addTruck.isPending}
                                    onClick={() => addTruck.mutate(i.id)}>Add to Direct trucks</button>)}
                          {i.status === "posted" && <button className="ml-2 text-[12.5px] text-steel underline" onClick={() => {
                            if (window.confirm(`Forget that ${i.invoice_no} was pushed? Only after deleting it in Tally.`)) post.mutate({ path: "/api/v1/purchases/forget", body: { ids: [i.id] } });
                          }}>Forget</button>}
                        </td>
                      </tr>))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {ready.length > 0 && (
            <div className="sticky bottom-0 mt-3 flex flex-wrap items-center justify-between gap-3 rounded-[6px] border border-seam bg-slab px-4 py-3 shadow-lg">
              <span className="num text-[13.5px]"><b>{sel.length}</b> of {ready.length} ready ticked · {fmtMoney(selTotal)}
                {!p.tally_checked && <span className="ml-2 text-warn">Tally duplicate check not run</span>}</span>
              <button className="btn-primary" disabled={!sel.length || post.isPending}
                      onClick={() => post.mutate({ path: "/api/v1/purchases/push", body: { ids: sel.map((i) => i.id) } })}>
                {post.isPending ? "Pushing…" : `Push ${sel.length} to Tally`}
              </button>
            </div>
          )}
        </>
      )}

      {xml && (
        <Drawer title="Tally XML" open onClose={() => setXml(null)}>
          <p className="mb-2 text-[12.5px] text-steel">Exactly what is sent to Tally for {xml.id.split(":")[1]}.</p>
          <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap rounded-[6px] bg-concrete p-3 text-[11.5px]">{xml.xml}</pre>
        </Drawer>
      )}
      {settings && <Settings onClose={() => setSettings(false)} onSaved={() => qc.invalidateQueries({ queryKey: ["purchases"] })} />}
    </>
  );
}

/** Red rows need a rule: which stock item a product text is, which godown a plant/depot goes to, or which
 *  Tally ledger the supplier is. A rule is saved once and applies to every later file. */
function Mapping({ items, options, onSave, busy }: {
  items: Unmapped[]; options: Payload["options"]; busy: boolean; onSave: (b: object) => void;
}) {
  const [val, setVal] = useState<Record<string, string>>({});
  const [kw, setKw] = useState<Record<string, string>>({});
  const key = (u: Unmapped) => `${u.kind}|${u.supplier}|${u.text}`;
  return (
    <div className="panel mb-4 border-warn/40">
      <div className="border-b border-seam px-4 py-2.5">
        <h2 className="font-semibold">Needs mapping</h2>
        <p className="text-[12.5px] text-steel">Pick the Tally name once; the rule is saved and used for every later file.</p>
      </div>
      <ul>
        {items.map((u) => {
          const k = key(u);
          const opts = u.kind === "product" ? options.items : u.kind === "godown" ? options.godowns : u.options ?? [];
          const v = val[k] ?? u.suggest?.[0] ?? "";
          return (
            <li key={k} className="flex flex-wrap items-end gap-3 border-t border-seam px-4 py-2.5 first:border-t-0">
              <div className="min-w-[200px] flex-1 text-[13.5px]">
                <div className="text-[12px] text-steel">{SUP[u.supplier]} · {u.kind === "product" ? "product" : u.kind === "godown" ? "source / plant" : "supplier ledger"} · {u.count} invoice{u.count > 1 ? "s" : ""}</div>
                <div className="font-semibold">{u.label ?? u.text}</div>
              </div>
              {u.kind !== "ledger" && (
                <label className="text-[12px] text-steel">Match text
                  <input className="input mt-0.5 h-8 w-44" placeholder={u.kind === "godown" && u.supplier === "ambuja" ? u.text.slice(0, 4) : (u.label ?? u.text).toUpperCase()}
                         value={kw[k] ?? ""} onChange={(e) => setKw({ ...kw, [k]: e.target.value })} />
                </label>)}
              <label className="text-[12px] text-steel">{u.kind === "product" ? "Tally stock item" : u.kind === "godown" ? "Tally godown" : "Tally ledger"}
                <select className="input mt-0.5 h-8 w-56" value={v} onChange={(e) => setVal({ ...val, [k]: e.target.value })}>
                  <option value="">Choose…</option>
                  {opts.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              </label>
              <button className="btn-quiet h-8" disabled={!v || busy}
                      onClick={() => onSave({ kind: u.kind, supplier: u.supplier, text: u.text, value: v, keyword: kw[k] || null })}>Save rule</button>
            </li>);
        })}
      </ul>
    </div>
  );
}

type Rule = { match: string; item?: string; godown?: string };
type Cfg = {
  company: string; voucher_type: string; purchase_ledger: string; bags_per_mt: number; use_bill_allocation: boolean;
  tally_host: string; tally_port: number;
  suppliers: Record<"ambuja" | "shree", { label: string; ledger: string; products: Rule[]; godowns: Rule[]; amount_field?: string }>;
};

/** The app's Settings: ledgers, voucher type, and the mapping rules (first match wins, top to bottom). */
function Settings({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const q = useQuery({ queryKey: ["purchase-settings"], queryFn: () => call(api.GET("/api/v1/purchases/settings")) as Promise<Cfg> });
  const [c, setC] = useState<Cfg | null>(null);
  const cfg = c ?? q.data ?? null;
  const save = useMutation({
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mutationFn: () => call(api.PUT("/api/v1/purchases/settings", { body: cfg as any })),
    onSuccess: () => { toast("ok", "Settings saved"); onSaved(); onClose(); },
    onError: (e) => toast("bad", problemMessage(e)),
  });
  const imp = useMutation({
    mutationFn: () => call(api.POST("/api/v1/purchases/import-uploader")),
    onSuccess: (r) => { toast("ok", `Imported from the Purchase Uploader app (${(r as { posted_imported: number }).posted_imported} pushed invoices remembered)`); setC(null); q.refetch(); onSaved(); },
    onError: (e) => toast("bad", e instanceof ApiError ? e.problem.detail : "Import failed"),
  });
  if (!cfg) return <Drawer title="Purchase settings" open onClose={onClose}><Loading /></Drawer>;
  const upd = (patch: Partial<Cfg>) => setC({ ...cfg, ...patch });
  const updSup = (s: "ambuja" | "shree", patch: Partial<Cfg["suppliers"]["ambuja"]>) =>
    upd({ suppliers: { ...cfg.suppliers, [s]: { ...cfg.suppliers[s], ...patch } } });
  return (
    <Drawer title="Purchase settings" open onClose={onClose}>
      <div className="space-y-4 text-[13.5px]">
        <p className="text-steel">Tally company <b className="text-ink">{cfg.company}</b>. The Tally address is the one used by Sync with Tally on the Overview page.</p>
        <Field label="Purchase ledger"><input className="input" value={cfg.purchase_ledger} onChange={(e) => upd({ purchase_ledger: e.target.value })} /></Field>
        <Field label="Voucher type"><input className="input" value={cfg.voucher_type} onChange={(e) => upd({ voucher_type: e.target.value })} /></Field>
        <Field label="Bags per MT"><input className="input num" inputMode="numeric" value={String(cfg.bags_per_mt)} onChange={(e) => upd({ bags_per_mt: Number(e.target.value) || 20 })} /></Field>
        {(["ambuja", "shree"] as const).map((s) => (
          <div key={s} className="rounded-[6px] border border-seam p-3">
            <h3 className="mb-2 font-semibold">{SUP[s]}</h3>
            <Field label="Supplier ledger"><input className="input" value={cfg.suppliers[s].ledger} onChange={(e) => updSup(s, { ledger: e.target.value })} /></Field>
            {(["products", "godowns"] as const).map((kind) => (
              <div key={kind} className="mt-3">
                <div className="label">{kind === "products" ? "Product rules (text → stock item)" : s === "ambuja" ? "Godown rules (invoice prefix → godown)" : "Godown rules (source text → godown)"}</div>
                {cfg.suppliers[s][kind].map((r, n) => (
                  <div key={n} className="mb-1 flex gap-1.5">
                    <input className="input h-8 flex-1" aria-label="Match" value={r.match}
                           onChange={(e) => { const a = [...cfg.suppliers[s][kind]]; a[n] = { ...r, match: e.target.value }; updSup(s, { [kind]: a }); }} />
                    <input className="input h-8 flex-1" aria-label={kind === "products" ? "Stock item" : "Godown"} value={(kind === "products" ? r.item : r.godown) ?? ""}
                           onChange={(e) => { const a = [...cfg.suppliers[s][kind]]; a[n] = { ...r, [kind === "products" ? "item" : "godown"]: e.target.value }; updSup(s, { [kind]: a }); }} />
                    <button className="btn-quiet h-8 px-2" aria-label="Remove rule"
                            onClick={() => updSup(s, { [kind]: cfg.suppliers[s][kind].filter((_, i) => i !== n) })}>×</button>
                  </div>))}
                <button className="text-[12.5px] text-steel underline"
                        onClick={() => updSup(s, { [kind]: [...cfg.suppliers[s][kind], kind === "products" ? { match: "", item: "" } : { match: "", godown: "" }] })}>+ add rule</button>
              </div>))}
            <p className="mt-2 text-[12px] text-steel">“A|B” matches either; “A&amp;B” needs both. First matching rule wins.</p>
          </div>))}
        <div className="flex flex-wrap gap-2">
          <button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>Save settings</button>
          <button className="btn-quiet" disabled={imp.isPending} onClick={() => {
            if (window.confirm("Replace these settings with the Purchase Uploader app's (from this Mac), and remember the invoices it already pushed?")) imp.mutate();
          }}>Import from Purchase Uploader app</button>
        </div>
      </div>
    </Drawer>
  );
}
