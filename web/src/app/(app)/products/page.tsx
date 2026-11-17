"use client";

import { useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useDebounced } from "@/lib/hooks";
import { useToast } from "@/components/toast";
import { Drawer, Empty, ErrorBox, Field, Loading, PageHeader, Pager, fieldErrors, fmtQty } from "@/components/ui";
import type { components } from "@/api/schema";

type Product = components["schemas"]["ProductOut"];
type View = "all" | "on" | "off" | "setup" | "general";
const CAT: Record<string, string> = { CEMENT: "Cement", TMT: "TMT", GENERAL: "Other material", OTHER: "Not classified" };

export default function ProductsPage() {
  const { can } = useAuth();
  const manage = can("masters.products_manage");
  const [view, setView] = useState<View>("on");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<Product | null>(null);
  const dq = useDebounced(q);
  const products = useQuery({
    queryKey: ["products", view, dq, page],
    queryFn: () => call(api.GET("/api/v1/products", { params: { query: {
      page, page_size: 100, q: dq || undefined,
      erp_enabled: view === "on" ? true : view === "off" ? false : undefined,
      needs_setup: view === "setup" ? true : undefined,
      category: view === "general" ? "GENERAL" : undefined,
    } } })),
    placeholderData: keepPreviousData,
  });
  const brands = useQuery({ queryKey: ["brands"], queryFn: () => call(api.GET("/api/v1/brands", { params: { query: { page_size: 200 } } })) });
  const bname = Object.fromEntries((brands.data?.items ?? []).map((b) => [b.id, b.name]));

  return (
    <>
      <PageHeader title="Products"
                  sub="Stock items come from Tally. Switch off the ones you don't sell, and finish any marked 'needs setup' so they can be ordered." />
      {view === "general" && (
        <p className="mb-3 rounded-[6px] border border-seam bg-slab px-4 py-3 text-[13.5px] text-steel">
          Blocks, bricks, sand, chemicals and other materials are set up in their Tally unit and will be switched on in the next phase.
        </p>
      )}
      <div className="grid gap-5 xl:grid-cols-[1fr_320px]">
        <div>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <div className="inline-flex rounded-[6px] border border-seam bg-slab p-0.5" role="group" aria-label="Filter">
              {([["on", "In use"], ["setup", "Needs setup"], ["general", "Other materials"], ["off", "Switched off"], ["all", "All"]] as const).map(([k, label]) => (
                <button key={k} onClick={() => { setView(k); setPage(1); }} aria-pressed={view === k}
                        className={`h-8 rounded-[4px] px-3 text-[13px] ${view === k ? "bg-blue text-white" : "text-steel hover:text-ink"}`}>{label}</button>
              ))}
            </div>
            <input className="input max-w-xs" placeholder="Search products" aria-label="Search products"
                   value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
          </div>
          {products.error ? <ErrorBox error={products.error} /> : (
            <div className="panel overflow-x-auto">
              {products.isLoading ? <Loading /> : products.data!.items.length === 0 ? (
                <Empty title={view === "setup" ? "Nothing needs setup" : "No products here"}
                       hint={view === "on" && !dq ? "Run the Tally sync to bring in your stock items." : undefined} />
              ) : (
                <table className="tbl">
                  <thead><tr><th>Product</th><th>Type</th><th>Brand</th><th>Pack / size</th><th>Stock unit</th><th className="w-36">Used in ERP</th><th /></tr></thead>
                  <tbody>
                    {products.data!.items.map((p) => (
                      <tr key={p.id} className={p.erp_enabled ? "" : "text-steel"}>
                        <td>
                          <div className="font-semibold">{p.name}</div>
                          {p.tally_parent && <div className="text-[12px] text-steel">Tally group: {p.tally_parent}</div>}
                        </td>
                        <td>{CAT[p.category]}</td>
                        <td>{p.brand_id ? bname[p.brand_id] ?? "…" : p.category === "GENERAL" ? "—" : <span className="text-warn">Not set</span>}</td>
                        <td className="num">{p.category === "CEMENT" ? `${fmtQty(p.bag_weight_kg)} kg bag`
                          : p.category === "TMT" ? `${p.diameter_mm} mm × ${fmtQty(p.length_m)} m`
                          : "—"}</td>
                        <td>{p.base_uom}</td>
                        <td>
                          {p.setup_missing.length > 0
                            ? <span className="text-[13px] text-warn">Needs {p.setup_missing.join(", ")}</span>
                            : !p.erp_enabled && p.enable_blocked_reason
                              ? <span className="text-[13px] text-steel" title={p.enable_blocked_reason}>
                                  {p.category === "GENERAL" ? "Next phase" : p.enable_blocked_reason}</span>
                              : <ProductSwitch p={p} disabled={!manage} />}
                        </td>
                        <td className="text-right">{manage && (
                          <button className={p.setup_missing.length ? "btn-primary h-8" : "btn-quiet h-8"} onClick={() => setEditing(p)}>
                            {p.setup_missing.length ? "Set up" : "Edit"}</button>)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {products.data && <Pager page={page} pageSize={100} total={products.data.total} onPage={setPage} />}
            </div>
          )}
        </div>
        <ConversionChecker />
      </div>
      {editing && <SetupProduct p={editing} brands={brands.data?.items ?? []} onClose={() => setEditing(null)} />}
    </>
  );
}

function ProductSwitch({ p, disabled }: { p: Product; disabled: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const m = useMutation({
    mutationFn: () => call(p.erp_enabled
      ? api.POST("/api/v1/products/{product_id}/erp-disable", { params: { path: { product_id: p.id } } })
      : api.POST("/api/v1/products/{product_id}/erp-enable", { params: { path: { product_id: p.id } } })),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["products"] }); toast("ok", `${r.name} ${r.erp_enabled ? "switched on" : "switched off"}`); },
    onError: (e) => toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"),
  });
  return (
    <button role="switch" aria-checked={p.erp_enabled} aria-label={`Use ${p.name} in ERP`} disabled={disabled || m.isPending}
            onClick={() => m.mutate()} className="inline-flex items-center gap-2 text-[13px] disabled:cursor-not-allowed">
      <span className={`relative h-[22px] w-10 rounded-full transition-colors ${p.erp_enabled ? "bg-ok" : "bg-seam"}`}>
        <span className={`absolute top-[3px] h-4 w-4 rounded-full bg-white shadow transition-[left] ${p.erp_enabled ? "left-[21px]" : "left-[3px]"}`} />
      </span>
      {p.erp_enabled ? "On" : "Off"}
    </button>
  );
}

type Brand = { id: string; name: string; category: string };

function SetupProduct({ p, brands, onClose }: { p: Product; brands: Brand[]; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const uoms = useQuery({ queryKey: ["uoms"], queryFn: () => call(api.GET("/api/v1/uoms")) as Promise<{ code: string; name: string }[]> });
  const [f, setF] = useState({
    base_uom: p.base_uom,
    category: p.category, brand_id: p.brand_id ?? "", bag_weight_kg: p.bag_weight_kg ?? "50",
    diameter_mm: p.diameter_mm?.toString() ?? "", cement_grade: p.cement_grade ?? "", tmt_grade: p.tmt_grade ?? "",
    switchOn: true,
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: async () => {
      const r = await call(api.PATCH("/api/v1/products/{product_id}", { params: { path: { product_id: p.id } }, body: {
        category: f.category as "CEMENT" | "TMT" | "GENERAL" | "OTHER",
        base_uom: f.category === "GENERAL" ? f.base_uom : null,
        brand_id: f.brand_id || null,
        bag_weight_kg: f.category === "CEMENT" ? String(f.bag_weight_kg) : null,
        diameter_mm: f.category === "TMT" && f.diameter_mm ? Number(f.diameter_mm) : null,
        cement_grade: f.category === "CEMENT" ? f.cement_grade || null : null,
        tmt_grade: f.category === "TMT" ? f.tmt_grade || null : null,
      } }));
      if (f.switchOn && !r.erp_enabled && r.setup_missing.length === 0) {
        return call(api.POST("/api/v1/products/{product_id}/erp-enable", { params: { path: { product_id: p.id } } }));
      }
      return r;
    },
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["products"] }); toast("ok", r.erp_enabled ? `${r.name} is ready to use` : `${r.name} saved`); onClose(); },
    onError: (e) => { setErrors(fieldErrors(e)); toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"); },
  });
  const brandChoices = brands.filter((b) => f.category === "OTHER" || f.category === "GENERAL" || b.category === f.category);
  return (
    <Drawer title={p.name} open onClose={onClose}>
      <p className="mb-4 text-[13px] text-steel">
        From Tally{p.tally_parent ? ` (group ${p.tally_parent}` : ""}{p.tally_base_unit ? `, unit ${p.tally_base_unit})` : p.tally_parent ? ")" : ""}.
        The name is Tally&apos;s. These details let orders and stock convert between bags, tonnes, pieces and kg.
      </p>
      <div className="space-y-4">
        <Field label="Type">
          <select className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value, brand_id: "" })}>
            <option value="CEMENT">Cement (stocked in bags)</option><option value="TMT">TMT (stocked in kg)</option>
            <option value="GENERAL">Other material (next phase)</option>
            <option value="OTHER">Not classified (cannot be ordered)</option>
          </select>
        </Field>
        {f.category === "GENERAL" && (
          <Field label="Stocked and sold in" error={errors.base_uom} hint={p.tally_base_unit ? `Tally unit: ${p.tally_base_unit}` : undefined}>
            <select className="input" value={f.base_uom} onChange={(e) => setF({ ...f, base_uom: e.target.value })}>
              {(uoms.data ?? []).map((u) => <option key={u.code} value={u.code}>{u.name}</option>)}
            </select>
          </Field>
        )}
        <Field label={f.category === "GENERAL" ? "Brand (optional)" : "Brand"} error={errors.brand_id}>
          <select className="input" value={f.brand_id} onChange={(e) => setF({ ...f, brand_id: e.target.value })}>
            <option value="">{f.category === "GENERAL" ? "None" : "Choose…"}</option>
            {brandChoices.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
        </Field>
        {f.category === "CEMENT" && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Bag weight (kg)" error={errors.bag_weight_kg} hint="50 kg = 20 bags per MT">
              <input className="input num" inputMode="decimal" value={f.bag_weight_kg} onChange={(e) => setF({ ...f, bag_weight_kg: e.target.value })} /></Field>
            <Field label="Grade"><input className="input" placeholder="PPC, OPC53…" value={f.cement_grade} onChange={(e) => setF({ ...f, cement_grade: e.target.value })} /></Field>
          </div>
        )}
        {f.category === "TMT" && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Diameter (mm)" error={errors.diameter_mm}>
              <input className="input num" inputMode="numeric" value={f.diameter_mm} onChange={(e) => setF({ ...f, diameter_mm: e.target.value })} /></Field>
            <Field label="Grade"><input className="input" placeholder="Fe550D" value={f.tmt_grade} onChange={(e) => setF({ ...f, tmt_grade: e.target.value })} /></Field>
          </div>
        )}
        {!p.erp_enabled && f.category !== "GENERAL" && (
          <label className="block text-[13.5px]"><input type="checkbox" checked={f.switchOn} onChange={(e) => setF({ ...f, switchOn: e.target.checked })} /> Switch on in the ERP after saving</label>
        )}
        <button className="btn-primary" disabled={save.isPending || (!["OTHER", "GENERAL"].includes(f.category) && !f.brand_id)} onClick={() => save.mutate()}>Save product</button>
      </div>
    </Drawer>
  );
}

function ConversionChecker() {
  const products = useQuery({ queryKey: ["products-ready"], queryFn: () => call(api.GET("/api/v1/products", {
    params: { query: { page_size: 200, erp_enabled: true } } })) });
  const list = products.data?.items ?? [];
  const [pid, setPid] = useState("");
  const [qty, setQty] = useState("25");
  const [from, setFrom] = useState("MT");
  const [to, setTo] = useState("");
  const p = list.find((x) => x.id === pid);
  const units = p ? Array.from(new Set([p.base_uom, ...p.units.map((u) => u.uom)])) : [];
  const conv = useMutation({
    mutationFn: () => call(api.POST("/api/v1/products/convert", { body: { product_id: pid, qty, from_uom: from, to_uom: to || null } })),
  });
  return (
    <aside className="panel h-fit p-4">
      <h2 className="display mb-1 text-[15px] font-bold">Check a conversion</h2>
      <p className="mb-4 text-[13px] text-steel">Uses the same rules as orders and stock, e.g. 25 MT of a 50 kg cement = 500 bags.</p>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); conv.mutate(); }}>
        <Field label="Product">
          <select className="input" value={pid} onChange={(e) => { setPid(e.target.value); conv.reset(); }}>
            <option value="">Choose…</option>{list.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          </select>
        </Field>
        <div className="grid grid-cols-[1fr_84px_84px] gap-2">
          <Field label="Quantity"><input className="input num" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} /></Field>
          <Field label="From"><select className="input" value={from} onChange={(e) => setFrom(e.target.value)}>{(units.length ? units : ["MT"]).map((u) => <option key={u}>{u}</option>)}</select></Field>
          <Field label="To"><select className="input" value={to} onChange={(e) => setTo(e.target.value)}>
            <option value="">{p?.base_uom ?? "Base"}</option>{units.map((u) => <option key={u}>{u}</option>)}</select></Field>
        </div>
        <button className="btn-primary" disabled={!pid || !qty || conv.isPending}>Convert</button>
      </form>
      {conv.data && (
        <p className="mt-4 border-t border-seam pt-3 text-[15px]" aria-live="polite">
          <span className="num">{fmtQty(conv.data.qty)} {conv.data.from_uom}</span> = <strong className="num">{fmtQty(conv.data.result, 6)} {conv.data.to_uom}</strong>
        </p>
      )}
      {conv.error && <p className="mt-3 text-[13px] text-bad">{conv.error instanceof ApiError ? conv.error.problem.detail : "Failed"}</p>}
    </aside>
  );
}
