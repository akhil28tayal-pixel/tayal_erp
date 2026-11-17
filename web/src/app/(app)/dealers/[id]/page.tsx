"use client";

import Link from "next/link";
import { use, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { WhatsAppBalanceButton } from "@/components/whatsapp";
import { ContactDrawer, useCanEditContacts, type Contact } from "@/components/contacts";
import { Badge, Drawer, ErrorBox, Field, Loading, PageHeader, fieldErrors, fmtMoney } from "@/components/ui";
import type { components } from "@/api/schema";

type Dealer = components["schemas"]["DealerDetailOut"];

export default function DealerPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { can } = useAuth();
  const dealer = useQuery({
    queryKey: ["dealer", id],
    queryFn: () => call(api.GET("/api/v1/dealers/{dealer_id}", { params: { path: { dealer_id: id } } })),
  });
  if (dealer.error) return <ErrorBox error={dealer.error} />;
  if (!dealer.data) return <Loading />;
  const d = dealer.data;

  return (
    <>
      <PageHeader back={{ href: "/dealers", label: "Dealers" }} title={d.name}
                  sub={<>Code {d.code}{d.tally_ledger_name ? <>. Tally ledger “{d.tally_ledger_name}” under {d.tally_parent_group ?? "—"}.</> : null}</>}
                  actions={<>
                    {can("credit.view") && <Link className="btn-quiet" href={`/dealers/${d.id}/statement`}>Statement</Link>}
                    {can("orders.create") && d.status !== "BLOCKED" && <Link className="btn-primary" href={`/orders/new?dealer=${d.id}`}>Book order</Link>}
                    {can("masters.dealers_manage") && <BlockButton d={d} />}</>} />
      {d.status === "BLOCKED" && (
        <div className="mb-4 rounded-[6px] border border-bad/30 bg-bad/5 px-4 py-3 text-[13.5px] text-bad">
          Blocked: {d.blocked_reason}. New orders cannot be submitted for this dealer.
        </div>
      )}
      <div className="grid gap-5 lg:grid-cols-[1fr_1fr]">
        <CreditPanel d={d} />
        <ContactsPanel d={d} />
        <AddressesPanel d={d} />
        <RatesPanel d={d} />
        <AssignmentsPanel d={d} />
      </div>
    </>
  );
}

function Section({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="panel">
      <div className="flex items-center justify-between border-b border-seam px-4 py-3">
        <h2 className="display text-[15px] font-bold">{title}</h2>{action}
      </div>
      {children}
    </section>
  );
}

function BlockButton({ d }: { d: Dealer }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const m = useMutation({
    mutationFn: () => d.status === "BLOCKED"
      ? call(api.POST("/api/v1/dealers/{dealer_id}/unblock", { params: { path: { dealer_id: d.id } } }))
      : call(api.POST("/api/v1/dealers/{dealer_id}/block", { params: { path: { dealer_id: d.id } }, body: { reason } })),
    onSuccess: (r) => { qc.setQueryData(["dealer", d.id], (old: Dealer) => ({ ...old, ...r })); setOpen(false);
      toast("ok", r.status === "BLOCKED" ? "Dealer blocked" : "Dealer unblocked"); },
    onError: (e) => toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"),
  });
  if (d.status === "BLOCKED") return <button className="btn-quiet" onClick={() => m.mutate()} disabled={m.isPending}>Unblock dealer</button>;
  return (
    <>
      <button className="btn-danger" onClick={() => setOpen(true)}>Block dealer</button>
      <Drawer title="Block dealer" open={open} onClose={() => setOpen(false)}>
        <p className="mb-4 text-[13.5px] text-steel">Blocked dealers cannot get new orders. Existing orders are not changed.</p>
        <Field label="Reason"><textarea className="input h-24 py-2" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <button className="btn-danger mt-4" disabled={reason.trim().length < 3 || m.isPending} onClick={() => m.mutate()}>Block dealer</button>
      </Drawer>
    </>
  );
}

function CreditPanel({ d }: { d: Dealer }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({
    credit_limit: d.credit_limit ?? "", payment_terms_days: d.payment_terms_days?.toString() ?? "",
    allowed_overdue_days: d.allowed_overdue_days?.toString() ?? "", credit_policy: d.credit_policy,
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: () => call(api.PATCH("/api/v1/dealers/{dealer_id}", {
      params: { path: { dealer_id: d.id } },
      body: {
        version: d.version,
        credit_limit: form.credit_limit === "" ? null : String(form.credit_limit),
        payment_terms_days: form.payment_terms_days === "" ? null : Number(form.payment_terms_days),
        allowed_overdue_days: form.allowed_overdue_days === "" ? null : Number(form.allowed_overdue_days),
        credit_policy: form.credit_policy as "STRICT" | "WARN" | "NONE",
      },
    })),
    onSuccess: () => { setErrors({}); qc.invalidateQueries({ queryKey: ["dealer", d.id] }); toast("ok", "Credit settings saved"); },
    onError: (e) => {
      setErrors(fieldErrors(e));
      if (e instanceof ApiError && e.problem.code === "VERSION_CONFLICT") {
        toast("bad", "Someone else changed this dealer. Reloaded the latest values; check them and save again.");
        qc.invalidateQueries({ queryKey: ["dealer", d.id] });
      } else toast("bad", e instanceof ApiError ? e.problem.detail : "Save failed");
    },
  });
  const editable = can("masters.dealers_manage");
  return (
    <Section title="Credit">
      <p className="num border-b border-seam px-4 py-3 text-[13.5px]">
        <span className="text-steel">Tally balance: </span>
        {d.tally_balance === null || d.tally_balance === undefined ? "not synced yet"
          : <><b>{fmtMoney(Math.abs(Number(d.tally_balance)))} {Number(d.tally_balance) >= 0 ? "Dr (dealer owes)" : "Cr (advance)"}</b>
              {d.tally_balance_at && <span className="text-steel"> · as of {new Date(d.tally_balance_at).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</span>}</>}
        {can("collections.followup") && Number(d.tally_balance ?? 0) > 0 && (
          <span className="ml-3 inline-block align-middle"><WhatsAppBalanceButton compact dealerId={d.id} dealerName={d.name} /></span>)}
      </p>
      <form className="grid grid-cols-2 gap-4 p-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Credit limit (₹)" error={errors.credit_limit}
               hint={d.tally_credit_limit ? `Tally shows ${fmtMoney(d.tally_credit_limit)} — reference only` : "Set here; Tally's limit is not used"}>
          <input className="input num" inputMode="decimal" disabled={!editable} value={form.credit_limit}
                 onChange={(e) => setForm({ ...form, credit_limit: e.target.value })} />
        </Field>
        <Field label="When over limit">
          <select className="input" disabled={!editable} value={form.credit_policy}
                  onChange={(e) => setForm({ ...form, credit_policy: e.target.value })}>
            <option value="STRICT">Hold the order for admin</option>
            <option value="WARN">Warn, allow approval</option>
            <option value="NONE">No credit check</option>
          </select>
        </Field>
        <Field label="Payment terms (days)" error={errors.payment_terms_days}
               hint={d.tally_credit_period_days ? `Tally credit period: ${d.tally_credit_period_days} days` : undefined}>
          <input className="input num" inputMode="numeric" disabled={!editable} value={form.payment_terms_days}
                 onChange={(e) => setForm({ ...form, payment_terms_days: e.target.value })} />
        </Field>
        <Field label="Allowed overdue (days)" error={errors.allowed_overdue_days}>
          <input className="input num" inputMode="numeric" disabled={!editable} value={form.allowed_overdue_days}
                 onChange={(e) => setForm({ ...form, allowed_overdue_days: e.target.value })} />
        </Field>
        {editable && <div className="col-span-2"><button className="btn-primary" disabled={save.isPending}>Save credit settings</button></div>}
      </form>
    </Section>
  );
}

function ContactsPanel({ d }: { d: Dealer }) {
  const qc = useQueryClient();
  const toast = useToast();
  const canEdit = useCanEditContacts();
  const [editing, setEditing] = useState<Contact | null | "new">(null);
  const remove = useMutation({
    mutationFn: (c: Contact) => call(api.DELETE("/api/v1/dealers/{dealer_id}/contacts/{contact_id}", { params: { path: { dealer_id: d.id, contact_id: c.id } } })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["dealer", d.id] }); qc.invalidateQueries({ queryKey: ["collections"] }); toast("ok", "Number removed"); },
    onError: (e) => toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"),
  });
  const fromTally = [d.tally_mobile && `Mobile: ${d.tally_mobile}`, d.tally_phone && `Phone: ${d.tally_phone}`].filter(Boolean).join(" · ");
  return (
    <Section title="Mobile numbers" action={canEdit && <button className="btn-quiet h-8" onClick={() => setEditing("new")}>Add mobile number</button>}>
      <ul className="divide-y divide-seam/70">
        {d.contacts.map((c) => (
          <li key={c.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-[13.5px]">
            <a className="num font-semibold hover:underline" href={`tel:${c.phone}`}>{c.phone ? `+91 ${c.phone.slice(0, 5)} ${c.phone.slice(5)}` : "—"}</a>
            <span>{c.name}{c.role ? <span className="text-steel"> · {c.role}</span> : null}</span>
            {c.is_primary && <Badge tone="ok">WhatsApp</Badge>}
            {canEdit && <span className="ml-auto flex gap-1">
              <button className="btn-quiet h-7 px-2 text-[12.5px]" onClick={() => setEditing(c)}>Edit</button>
              <button className="btn-quiet h-7 px-2 text-[12.5px]" disabled={remove.isPending}
                      onClick={() => { if (window.confirm(`Remove ${c.phone} (${c.name})?`)) remove.mutate(c); }}>Remove</button>
            </span>}
          </li>
        ))}
        <li className="px-4 py-3 text-[13px] text-steel">
          {fromTally ? <>From Tally — {fromTally}{d.contacts.length > 0 ? " (numbers above are used first)" : ""}</>
            : d.contacts.length === 0 ? "No mobile number in Tally or here yet. Add one to send WhatsApp reminders." : "No number in the Tally ledger."}
        </li>
      </ul>
      <ContactDrawer dealerId={d.id} dealerName={d.name} open={editing !== null} onClose={() => setEditing(null)}
                     contact={editing === "new" ? null : editing} />
    </Section>
  );
}

function AddressesPanel({ d }: { d: Dealer }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const blank = { kind: "SITE" as "SITE" | "DELIVERY" | "BILLING", label: "", line1: "", area: "", city: "Delhi", pincode: "" };
  const [f, setF] = useState(blank);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const add = useMutation({
    mutationFn: () => call(api.POST("/api/v1/dealers/{dealer_id}/addresses", {
      params: { path: { dealer_id: d.id } },
      body: { ...f, line1: f.line1 || null, area: f.area || null, pincode: f.pincode || null, is_active: true },
    })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["dealer", d.id] }); setOpen(false); setF(blank); toast("ok", "Site added"); },
    onError: (e) => { setErrors(fieldErrors(e)); toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"); },
  });
  return (
    <Section title="Delivery sites"
             action={can("masters.dealers_manage") && <button className="btn-quiet h-8" onClick={() => setOpen(true)}>Add site</button>}>
      {d.addresses.length === 0 ? (
        <p className="px-4 py-6 text-[13.5px] text-steel">No sites yet. Add the construction sites and delivery points used for this dealer.</p>
      ) : (
        <ul className="divide-y divide-seam/70">
          {d.addresses.map((a) => (
            <li key={a.id} className="px-4 py-3">
              <div className="font-semibold">{a.label} <Badge>{a.kind.toLowerCase()}</Badge></div>
              <div className="text-[13px] text-steel">{[a.line1, a.area, a.city, a.pincode].filter(Boolean).join(", ")}</div>
            </li>
          ))}
        </ul>
      )}
      <Drawer title="Add delivery site" open={open} onClose={() => setOpen(false)}>
        <div className="space-y-4">
          <Field label="Type">
            <select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as typeof f.kind })}>
              <option value="SITE">Construction site</option><option value="DELIVERY">Delivery point</option><option value="BILLING">Billing address</option>
            </select>
          </Field>
          <Field label="Name" error={errors.label}><input className="input" placeholder="e.g. Sector 8 site" value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} /></Field>
          <Field label="Address"><input className="input" value={f.line1} onChange={(e) => setF({ ...f, line1: e.target.value })} /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Area"><input className="input" value={f.area} onChange={(e) => setF({ ...f, area: e.target.value })} /></Field>
            <Field label="PIN code" error={errors.pincode}><input className="input num" inputMode="numeric" value={f.pincode} onChange={(e) => setF({ ...f, pincode: e.target.value })} /></Field>
          </div>
          <button className="btn-primary" disabled={!f.label || add.isPending} onClick={() => add.mutate()}>Add site</button>
        </div>
      </Drawer>
    </Section>
  );
}

function RatesPanel({ d }: { d: Dealer }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const rates = useQuery({ queryKey: ["dealer-rates", d.id],
    queryFn: () => call(api.GET("/api/v1/dealers/{dealer_id}/rates", { params: { path: { dealer_id: d.id } } })) });
  const products = useQuery({ queryKey: ["products", "rate-picker"],
    queryFn: () => call(api.GET("/api/v1/products", { params: { query: { page_size: 200, erp_enabled: true } } })) });
  const godowns = useQuery({ queryKey: ["godowns-enabled"],
    queryFn: () => call(api.GET("/api/v1/godowns", { params: { query: { page_size: 200, erp_enabled: true } } })) });
  const [open, setOpen] = useState(false);
  const today = new Date().toISOString().slice(0, 10);
  const [f, setF] = useState({ product_id: "", godown_id: "", uom: "BAG", rate: "", tolerance_pct: "0", valid_from: today });
  const pmap = Object.fromEntries((products.data?.items ?? []).map((p) => [p.id, p]));
  const gmap = Object.fromEntries((godowns.data?.items ?? []).map((g) => [g.id, g.name]));
  const add = useMutation({
    mutationFn: () => call(api.POST("/api/v1/dealers/{dealer_id}/rates", {
      params: { path: { dealer_id: d.id } },
      body: { product_id: f.product_id, godown_id: f.godown_id || null, uom: f.uom, rate: f.rate,
              tolerance_pct: f.tolerance_pct || "0", valid_from: f.valid_from },
    })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["dealer-rates", d.id] }); setOpen(false); toast("ok", "Rate saved"); },
    onError: (e) => toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"),
  });
  const current = (rates.data ?? []).filter((r) => !r.valid_to || r.valid_to >= today);
  const selected = pmap[f.product_id];

  return (
    <Section title="Customer rates"
             action={can("masters.prices_manage") && <button className="btn-quiet h-8" onClick={() => setOpen(true)}>Set rate</button>}>
      {rates.isLoading ? <Loading /> : current.length === 0 ? (
        <p className="px-4 py-6 text-[13.5px] text-steel">No rates set. Orders for this dealer will need a rate entered and approved.</p>
      ) : (
        <table className="tbl">
          <thead><tr><th>Product</th><th>Godown</th><th className="text-right">Rate</th><th className="text-right">Min. without approval</th><th>From</th></tr></thead>
          <tbody>
            {current.map((r) => {
              const min = Number(r.rate) * (1 - Number(r.tolerance_pct) / 100);
              return (
                <tr key={r.id}>
                  <td>{pmap[r.product_id]?.name ?? "…"}</td>
                  <td className="text-steel">{r.godown_id ? gmap[r.godown_id] ?? "—" : "Any"}</td>
                  <td className="num text-right">{fmtMoney(r.rate)} / {r.uom.toLowerCase()}</td>
                  <td className="num text-right text-steel">{fmtMoney(min.toFixed(2))}</td>
                  <td className="num text-steel">{r.valid_from}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <Drawer title="Set customer rate" open={open} onClose={() => setOpen(false)}>
        <div className="space-y-4">
          <Field label="Product">
            <select className="input" value={f.product_id}
                    onChange={(e) => { const p = pmap[e.target.value]; setF({ ...f, product_id: e.target.value, uom: p?.base_uom ?? "BAG" }); }}>
              <option value="">Choose…</option>
              {(products.data?.items ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </Field>
          <Field label="Godown" hint="Leave as 'Any godown' unless this dealer pays a different rate per godown.">
            <select className="input" value={f.godown_id} onChange={(e) => setF({ ...f, godown_id: e.target.value })}>
              <option value="">Any godown</option>
              {(godowns.data?.items ?? []).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
            </select>
          </Field>
          <div className="grid grid-cols-[1fr_120px] gap-3">
            <Field label="Rate (₹)"><input className="input num" inputMode="decimal" value={f.rate} onChange={(e) => setF({ ...f, rate: e.target.value })} /></Field>
            <Field label="Per">
              <select className="input" value={f.uom} onChange={(e) => setF({ ...f, uom: e.target.value })}>
                {(selected?.units ?? [{ uom: f.uom }]).map((u) => <option key={u.uom} value={u.uom}>{u.uom}</option>)}
              </select>
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Tolerance %" hint="How far below the rate sales can go without approval">
              <input className="input num" inputMode="decimal" value={f.tolerance_pct} onChange={(e) => setF({ ...f, tolerance_pct: e.target.value })} /></Field>
            <Field label="Applies from"><input type="date" className="input" value={f.valid_from} onChange={(e) => setF({ ...f, valid_from: e.target.value })} /></Field>
          </div>
          <p className="text-[12.5px] text-steel">The previous rate for this product stops on this date. Old rates are kept for history.</p>
          <button className="btn-primary" disabled={!f.product_id || !f.rate || add.isPending} onClick={() => add.mutate()}>Save rate</button>
        </div>
      </Drawer>
    </Section>
  );
}

function AssignmentsPanel({ d }: { d: Dealer }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const hist = useQuery({ queryKey: ["dealer-assign", d.id],
    queryFn: () => call(api.GET("/api/v1/dealers/{dealer_id}/assignments", { params: { path: { dealer_id: d.id } } })) });
  const emps = useQuery({ queryKey: ["employees"],
    queryFn: () => call(api.GET("/api/v1/employees", { params: { query: { page_size: 200, active: true } } })) });
  const ename = Object.fromEntries((emps.data?.items ?? []).map((e) => [e.id, e.full_name]));
  const [kind, setKind] = useState<"SALES" | "COLLECTION">("COLLECTION");
  const [emp, setEmp] = useState("");
  const assign = useMutation({
    mutationFn: () => call(api.POST("/api/v1/dealers/assignments/bulk", {
      body: { dealer_ids: [d.id], employee_id: emp, kind, reason: null } })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["dealer-assign", d.id] }); toast("ok", "Assigned"); setEmp(""); },
    onError: (e) => toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"),
  });
  const open = (k: string) => hist.data?.find((a) => a.kind === k && !a.valid_to);
  return (
    <Section title="Sales & collection owner">
      <dl className="grid grid-cols-2 gap-4 p-4 text-[13.5px]">
        <div><dt className="label">Sales executive</dt><dd className="font-semibold">{open("SALES") ? ename[open("SALES")!.employee_id] ?? "…" : "Unassigned"}</dd></div>
        <div><dt className="label">Collection executive</dt><dd className="font-semibold">{open("COLLECTION") ? ename[open("COLLECTION")!.employee_id] ?? "…" : "Unassigned"}</dd></div>
      </dl>
      {can("collections.assign") && emps.data && emps.data.items.length === 0 && (
        <p className="border-t border-seam px-4 py-3 text-[13.5px] text-steel">
          No staff to assign yet. Add your sales and collection staff on the{" "}
          <Link href="/users" className="font-semibold text-ink underline">Users</Link> page; each user can then be assigned here.
        </p>
      )}
      {can("collections.assign") && (emps.data?.items.length ?? 0) > 0 && (
        <div className="flex flex-wrap items-end gap-2 border-t border-seam px-4 py-3">
          <select className="input w-36" aria-label="Assignment type" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
            <option value="COLLECTION">Collection</option><option value="SALES">Sales</option>
          </select>
          <select className="input w-56" aria-label="Employee" value={emp} onChange={(e) => setEmp(e.target.value)}>
            <option value="">Choose employee…</option>
            {(emps.data?.items ?? []).map((e) => <option key={e.id} value={e.id}>{e.full_name}</option>)}
          </select>
          <button className="btn-quiet" disabled={!emp || assign.isPending} onClick={() => assign.mutate()}>Assign</button>
        </div>
      )}
      {(hist.data?.length ?? 0) > 0 && (
        <details className="border-t border-seam px-4 py-3 text-[13px]">
          <summary className="cursor-pointer text-steel">History ({hist.data!.length})</summary>
          <ul className="mt-2 space-y-1">
            {hist.data!.map((a) => (
              <li key={a.id} className="num text-steel">{a.kind.toLowerCase()}: {ename[a.employee_id] ?? "…"}, {a.valid_from} to {a.valid_to ?? "now"}</li>
            ))}
          </ul>
        </details>
      )}
    </Section>
  );
}
