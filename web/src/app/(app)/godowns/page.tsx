"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { Drawer, Empty, ErrorBox, Field, Loading, PageHeader } from "@/components/ui";
import type { components } from "@/api/schema";

type Godown = components["schemas"]["GodownOut"];
const KIND_LABEL: Record<string, string> = { GODOWN: "Godown", DIRECT: "Direct (factory to site)", SIDING: "Rail siding", VIRTUAL: "System" };

export default function GodownsPage() {
  const { can } = useAuth();
  const manage = can("masters.godowns_manage");
  const [show, setShow] = useState<"all" | "on" | "off">("all");
  const [editing, setEditing] = useState<Godown | null>(null);
  const list = useQuery({
    queryKey: ["godowns", show],
    queryFn: () => call(api.GET("/api/v1/godowns", { params: { query: {
      page_size: 200, erp_enabled: show === "all" ? undefined : show === "on" } } })),
  });

  return (
    <>
      <PageHeader title="Godowns"
                  sub="Names come from Tally. Switch off godowns you don't use. They stay in Tally but won't appear in orders, stock or dispatch." />
      <div className="mb-3 inline-flex rounded-[6px] border border-seam bg-slab p-0.5" role="group" aria-label="Filter">
        {(["all", "on", "off"] as const).map((k) => (
          <button key={k} onClick={() => setShow(k)} aria-pressed={show === k}
                  className={`h-8 rounded-[4px] px-3 text-[13px] ${show === k ? "bg-blue text-white" : "text-steel hover:text-ink"}`}>
            {k === "all" ? "All" : k === "on" ? "In use" : "Switched off"}
          </button>
        ))}
      </div>
      {list.error ? <ErrorBox error={list.error} /> : (
        <div className="panel overflow-x-auto">
          {list.isLoading ? <Loading /> : list.data!.items.length === 0 ? (
            <Empty title={show === "all" ? "No godowns yet" : "Nothing here"}
                   hint={show === "all" ? "Run the Tally sync to bring in your godowns." : undefined} />
          ) : (
            <table className="tbl">
              <thead><tr><th>Godown</th><th>Type</th><th>City</th><th>Products</th><th className="w-44">Used in ERP</th><th /></tr></thead>
              <tbody>
                {list.data!.items.map((g) => (
                  <tr key={g.id} className={g.erp_enabled ? "" : "text-steel"}>
                    <td>
                      <div className="font-semibold">{g.name}</div>
                      {g.aliases.length > 0 && <div className="text-[12px] text-steel">also called {g.aliases.join(", ")}</div>}
                    </td>
                    <td>{KIND_LABEL[g.kind]}</td>
                    <td>{g.city ?? "—"}</td>
                    <td className="text-[13px]">{g.supported_categories.map((c) => c === "TMT" ? "TMT" : "Cement").join(" & ")}</td>
                    <td><ErpSwitch g={g} disabled={!manage} /></td>
                    <td className="text-right">{manage && <button className="btn-quiet h-8" onClick={() => setEditing(g)}>Edit</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
      {editing && <EditGodown g={editing} onClose={() => setEditing(null)} />}
    </>
  );
}

function ErpSwitch({ g, disabled }: { g: Godown; disabled: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const m = useMutation({
    mutationFn: () => call(g.erp_enabled
      ? api.POST("/api/v1/godowns/{godown_id}/erp-disable", { params: { path: { godown_id: g.id } } })
      : api.POST("/api/v1/godowns/{godown_id}/erp-enable", { params: { path: { godown_id: g.id } } })),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["godowns"] }); toast("ok", `${r.name} ${r.erp_enabled ? "switched on" : "switched off"}`); },
    onError: (e) => toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"),
  });
  if (g.tally_deleted) return <span className="text-[13px] text-bad">Deleted in Tally</span>;
  return (
    <button role="switch" aria-checked={g.erp_enabled} aria-label={`Use ${g.name} in ERP`} disabled={disabled || m.isPending}
            onClick={() => m.mutate()}
            className="group inline-flex items-center gap-2 text-[13px] disabled:cursor-not-allowed">
      <span className={`relative h-[22px] w-10 rounded-full transition-colors ${g.erp_enabled ? "bg-ok" : "bg-seam"}`}>
        <span className={`absolute top-[3px] h-4 w-4 rounded-full bg-white shadow transition-[left] ${g.erp_enabled ? "left-[21px]" : "left-[3px]"}`} />
      </span>
      {g.erp_enabled ? "On" : "Off"}
    </button>
  );
}

function EditGodown({ g, onClose }: { g: Godown; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState({ kind: g.kind, city: g.city ?? "", contact_person: g.contact_person ?? "", phone: g.phone ?? "",
    address: g.address ?? "", cement: g.supported_categories.includes("CEMENT"), tmt: g.supported_categories.includes("TMT"),
    aliases: g.aliases.join(", ") });
  const save = useMutation({
    mutationFn: () => call(api.PATCH("/api/v1/godowns/{obj_id}", { params: { path: { obj_id: g.id } }, body: {
      kind: f.kind, city: f.city || null, contact_person: f.contact_person || null, phone: f.phone || null, address: f.address || null,
      supported_categories: [...(f.cement ? ["CEMENT" as const] : []), ...(f.tmt ? ["TMT" as const] : [])],
      aliases: f.aliases.split(",").map((s) => s.trim()).filter(Boolean),
    } })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["godowns"] }); toast("ok", "Godown saved"); onClose(); },
    onError: (e) => toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"),
  });
  return (
    <Drawer title={g.name} open onClose={onClose}>
      <p className="mb-4 text-[13px] text-steel">The name comes from Tally. Rename it in Tally and run the sync.</p>
      <div className="space-y-4">
        <Field label="Type">
          <select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as typeof f.kind })}>
            {Object.entries(KIND_LABEL).filter(([k]) => k !== "VIRTUAL").map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Field>
        <fieldset>
          <legend className="label">Stocks</legend>
          <label className="mr-4 text-[13.5px]"><input type="checkbox" checked={f.cement} onChange={(e) => setF({ ...f, cement: e.target.checked })} /> Cement</label>
          <label className="text-[13.5px]"><input type="checkbox" checked={f.tmt} onChange={(e) => setF({ ...f, tmt: e.target.checked })} /> TMT</label>
        </fieldset>
        <Field label="Other names used for it" hint="Comma separated, e.g. Palam"><input className="input" value={f.aliases} onChange={(e) => setF({ ...f, aliases: e.target.value })} /></Field>
        <Field label="Address"><input className="input" value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="City"><input className="input" value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} /></Field>
          <Field label="Phone"><input className="input num" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
        </div>
        <Field label="Contact person"><input className="input" value={f.contact_person} onChange={(e) => setF({ ...f, contact_person: e.target.value })} /></Field>
        <button className="btn-primary" disabled={save.isPending || (!f.cement && !f.tmt)} onClick={() => save.mutate()}>Save godown</button>
      </div>
    </Drawer>
  );
}
