"use client";

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { Drawer, Field, fieldErrors } from "@/components/ui";
import type { components } from "@/api/schema";

export type Contact = components["schemas"]["DealerContactOut"];

export const useCanEditContacts = () => {
  const { can } = useAuth();
  return can("masters.dealers_manage") || can("collections.followup");
};

/** Add or edit one dealer contact. The primary contact's mobile is the one WhatsApp messages go to. */
export function ContactDrawer({ dealerId, dealerName, contact, open, onClose, onSaved }: {
  dealerId: string; dealerName: string; contact?: Contact | null; open: boolean; onClose: () => void; onSaved?: () => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const blank = { name: "", phone: "", role: "", is_primary: true };
  const [f, setF] = useState(blank);
  const [errors, setErrors] = useState<Record<string, string>>({});
  useEffect(() => {
    if (open) {
      setErrors({});
      setF(contact ? { name: contact.name, phone: contact.phone ?? "", role: contact.role ?? "", is_primary: contact.is_primary } : blank);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, contact?.id]);
  const save = useMutation({
    mutationFn: () => {
      const body = { name: f.name.trim(), phone: f.phone.trim() || null, role: f.role.trim() || null, is_primary: f.is_primary, whatsapp_opt_in: false };
      return contact
        ? call(api.PUT("/api/v1/dealers/{dealer_id}/contacts/{contact_id}", { params: { path: { dealer_id: dealerId, contact_id: contact.id } }, body }))
        : call(api.POST("/api/v1/dealers/{dealer_id}/contacts", { params: { path: { dealer_id: dealerId } }, body }));
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["dealer", dealerId] });
      qc.invalidateQueries({ queryKey: ["collections"] });
      toast("ok", contact ? "Number updated" : `Number saved for ${dealerName}`);
      onSaved?.();
      onClose();
    },
    onError: (e) => { setErrors(fieldErrors(e)); toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"); },
  });
  return (
    <Drawer title={contact ? "Edit mobile number" : `Add mobile number · ${dealerName}`} open={open} onClose={onClose}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Mobile number" error={errors.phone} hint="10 digits. +91 or a leading 0 is fine.">
          <input className="input num" inputMode="tel" autoFocus value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} />
        </Field>
        <Field label="Name" error={errors.name} hint="Who answers this number, e.g. Owner, Munshi, Accounts">
          <input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        </Field>
        <Field label="Role (optional)"><input className="input" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })} /></Field>
        <label className="flex items-start gap-2 text-[13.5px]">
          <input type="checkbox" className="mt-1" checked={f.is_primary} onChange={(e) => setF({ ...f, is_primary: e.target.checked })} />
          <span>Send WhatsApp messages to this number<span className="block text-[12px] text-steel">Used for balance reminders instead of the number in Tally</span></span>
        </label>
        <button className="btn-primary" disabled={!f.name.trim() || !f.phone.trim() || save.isPending}>{contact ? "Save" : "Add number"}</button>
      </form>
    </Drawer>
  );
}
