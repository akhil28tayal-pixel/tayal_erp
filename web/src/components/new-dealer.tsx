"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, call } from "@/lib/api";
import { useToast } from "@/components/toast";
import { Drawer, Field } from "@/components/ui";
import { problemMessage } from "@/components/orders";

/** Add a dealer who isn't in Tally yet, while booking. It is also made as a ledger in Tally (now, or at the next
 *  Tally sync if Tally can't be reached). Calls onMade with the new dealer's id. */
export function NewDealerDrawer({ open, onClose, startName, onMade }: {
  open: boolean; onClose: () => void; startName: string; onMade: (id: string) => void;
}) {
  const toast = useToast();
  const qc = useQueryClient();
  const blank = { name: "", mobile: "", contact_name: "", gstin: "", address: "", city: "", state: "Delhi", pincode: "", group: "" };
  const [f, setF] = useState(blank);
  const [exists, setExists] = useState<{ id: string; name: string } | null>(null);
  useEffect(() => { if (open) { setF({ ...blank, name: startName }); setExists(null); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const groups = useQuery({ queryKey: ["dealer-groups"], enabled: open,
    queryFn: () => call(api.GET("/api/v1/dealers/tally-groups")) as Promise<string[]> });
  const set = (k: keyof typeof blank) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const save = useMutation({
    mutationFn: () => call(api.POST("/api/v1/dealers", { body: {
      name: f.name.trim(), mobile: f.mobile || null, contact_name: f.contact_name || null, gstin: f.gstin || null,
      address: f.address || null, city: f.city || null, state: f.state || null, pincode: f.pincode || null,
      group: f.group || null, make_in_tally: true,
    } })),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["dealer-pick"] });
      toast("ok", `${r.dealer.name} added. ${r.tally}`);
      onMade(r.dealer.id);
      onClose();
    },
    onError: (e) => {
      if (e instanceof ApiError && e.problem.code === "DEALER_EXISTS") {
        const p = e.problem as unknown as { dealer_id: string; dealer_name: string };
        setExists({ id: p.dealer_id, name: p.dealer_name });
      } else toast("bad", problemMessage(e));
    },
  });
  const mobileOk = !f.mobile || f.mobile.replace(/\D/g, "").slice(-10).length === 10;
  const gstOk = !f.gstin || /^[0-9]{2}[A-Za-z0-9]{13}$/.test(f.gstin.trim());
  const pinOk = !f.pincode || /^[0-9]{6}$/.test(f.pincode);
  const ready = f.name.trim().length >= 2 && mobileOk && gstOk && pinOk && !save.isPending;

  return (
    <Drawer title="New dealer" open={open} onClose={onClose}>
      <p className="mb-3 text-[13px] text-steel">For a dealer who isn&apos;t in Tally yet. They are added here and made as a ledger in Tally
        {" "}(right away, or at the next Tally sync if Tally isn&apos;t open).</p>
      <div className="space-y-3">
        <Field label="Name, as it should be in Tally"><input className="input" autoFocus value={f.name} onChange={set("name")} /></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Mobile (for WhatsApp)" error={mobileOk ? undefined : "10-digit mobile number"}>
            <input className="input num" inputMode="tel" value={f.mobile} onChange={set("mobile")} /></Field>
          <Field label="Contact person (optional)"><input className="input" value={f.contact_name} onChange={set("contact_name")} /></Field>
        </div>
        <Field label="Address (optional)"><input className="input" value={f.address} onChange={set("address")} /></Field>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="City / area"><input className="input" value={f.city} onChange={set("city")} /></Field>
          <Field label="State"><input className="input" value={f.state} onChange={set("state")} /></Field>
          <Field label="PIN code" error={pinOk ? undefined : "6 digits"}><input className="input num" inputMode="numeric" value={f.pincode} onChange={set("pincode")} /></Field>
        </div>
        <Field label="GSTIN (if registered)" error={gstOk ? undefined : "15 characters, e.g. 07ABCDE1234F1Z5"}>
          <input className="input num uppercase" value={f.gstin} onChange={set("gstin")} /></Field>
        <Field label="Tally group">
          <select className="input" value={f.group} onChange={set("group")}>
            <option value="">{groups.data?.[0] ?? "Sundry Debtors"}</option>
            {(groups.data ?? []).slice(1).map((g) => <option key={g} value={g}>{g}</option>)}
          </select>
        </Field>
        {exists && (
          <div className="rounded-[6px] bg-warn/10 px-3 py-2 text-[13px]">
            <b>{exists.name}</b> is already a dealer.{" "}
            <button className="text-blue underline" onClick={() => { onMade(exists.id); onClose(); }}>Book for {exists.name}</button>
          </div>)}
      </div>
      <div className="mt-5 flex gap-2">
        <button className="btn-primary" disabled={!ready} onClick={() => save.mutate()}>{save.isPending ? "Adding…" : "Add dealer"}</button>
        <button className="btn-quiet" onClick={onClose}>Cancel</button>
      </div>
    </Drawer>
  );
}
