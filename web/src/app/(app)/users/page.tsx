"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { Badge, Drawer, Empty, ErrorBox, Field, Loading, PageHeader, fieldErrors } from "@/components/ui";
import { roleName } from "@/components/shell";
import type { components } from "@/api/schema";

type User = components["schemas"]["UserListOut"];

/** What each role can do, in plain words, shown next to the role checkboxes. */
const ROLE_HELP: Record<string, string> = {
  SUPER_ADMIN: "Everything, including users, roles and settings",
  BUSINESS_OWNER: "All operations, approvals and reports; releases credit holds",
  OPERATIONS_MANAGER: "Approves orders, plans dispatch (vehicle, driver, loading), manages stock",
  ACCOUNTS_EXECUTIVE: "Tally sync, outstanding, payment verification, dealer credit settings",
  SALES_EXECUTIVE: "Books orders for assigned dealers. Nothing else.",
  COLLECTION_EXECUTIVE: "Follow-ups, payment promises and payment reports for assigned dealers",
  GODOWN_SUPERVISOR: "Stock, receipts, loading and transfers for chosen godowns only",
  DRIVER: "Own trips: delivery status and proof of delivery",
};
const ROLE_ORDER = ["SALES_EXECUTIVE", "OPERATIONS_MANAGER", "COLLECTION_EXECUTIVE", "GODOWN_SUPERVISOR", "DRIVER",
  "ACCOUNTS_EXECUTIVE", "BUSINESS_OWNER", "SUPER_ADMIN"];

export default function UsersPage() {
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const users = useQuery({ queryKey: ["users"], queryFn: () => call(api.GET("/api/v1/users", { params: { query: { page_size: 200 } } })) });
  return (
    <>
      <PageHeader title="Users" sub="People who can sign in to this company on the web or the Android app. A user with several roles can do everything those roles allow."
                  actions={<button className="btn-primary" onClick={() => setAdding(true)}>Add user</button>} />
      <div className="panel overflow-x-auto">
        {users.error ? <ErrorBox error={users.error} /> : users.isLoading ? <Loading /> : users.data!.items.length === 0 ? <Empty title="No users" /> : (
          <table className="tbl">
            <thead><tr><th>Name</th><th>Phone</th><th>Roles</th><th>Status</th><th /></tr></thead>
            <tbody>{users.data!.items.map((u) => (
              <tr key={u.id} className={u.is_active ? "" : "text-steel"}>
                <td className="font-semibold">{u.full_name}</td>
                <td className="num">{u.phone}</td>
                <td>
                  {u.roles.length === 0 ? <span className="text-warn">No role, cannot sign in here</span> : (
                    <div className="flex flex-wrap gap-1">{u.roles.map((r) => <Badge key={r}>{roleName(r)}</Badge>)}</div>
                  )}
                </td>
                <td><Badge tone={u.is_active ? "ok" : "neutral"}>{u.is_active ? "active" : "deactivated"}</Badge></td>
                <td className="text-right"><button className="btn-quiet h-8" onClick={() => setEditing(u)}>Edit</button></td>
              </tr>))}
            </tbody>
          </table>
        )}
      </div>
      <AddUser open={adding} onClose={() => setAdding(false)} />
      {editing && <EditUser user={editing} onClose={() => setEditing(null)} />}
    </>
  );
}

function useRoleChoices(enabled: boolean) {
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => call(api.GET("/api/v1/roles")), enabled });
  const godowns = useQuery({ queryKey: ["godowns-enabled"], enabled,
    queryFn: () => call(api.GET("/api/v1/godowns", { params: { query: { page_size: 200, erp_enabled: true } } })) });
  const codes = (roles.data ?? []).map((r) => r.code).sort((a, b) => ROLE_ORDER.indexOf(a) - ROLE_ORDER.indexOf(b));
  return { codes, godowns: godowns.data?.items ?? [] };
}

const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

function RolePicker({ value, onChange, godownIds, onGodowns, enabled }: {
  value: string[]; onChange: (v: string[]) => void; godownIds: string[]; onGodowns: (v: string[]) => void; enabled: boolean;
}) {
  const { codes, godowns } = useRoleChoices(enabled);
  return (
    <fieldset>
      <legend className="label">Roles</legend>
      <div className="space-y-1.5">
        {codes.map((code) => (
          <label key={code} className={`flex gap-3 rounded-[6px] border px-3 py-2 ${value.includes(code) ? "border-blue bg-blue-tint" : "border-seam"}`}>
            <input type="checkbox" className="mt-1" checked={value.includes(code)} onChange={() => onChange(toggle(value, code))} />
            <span>
              <span className="block text-[13.5px] font-semibold">{roleName(code)}</span>
              <span className="block text-[12.5px] text-steel">{ROLE_HELP[code] ?? ""}</span>
            </span>
          </label>
        ))}
      </div>
      {value.includes("SALES_EXECUTIVE") && value.includes("OPERATIONS_MANAGER") && (
        <p className="mt-2 text-[12.5px] text-steel">Sales + Operations: books orders and fills in dispatch details.</p>
      )}
      {value.includes("GODOWN_SUPERVISOR") && (
        <div className="mt-3">
          <span className="label">Godowns this supervisor handles</span>
          {godowns.length === 0 ? <p className="text-[13px] text-warn">No godowns switched on yet.</p> : godowns.map((g) => (
            <label key={g.id} className="mr-4 inline-block text-[13.5px]">
              <input type="checkbox" checked={godownIds.includes(g.id)} onChange={() => onGodowns(toggle(godownIds, g.id))} /> {g.name}
            </label>
          ))}
        </div>
      )}
    </fieldset>
  );
}

function AddUser({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const blank = { full_name: "", phone: "", password: "", roles: [] as string[], godown_ids: [] as string[] };
  const [f, setF] = useState(blank);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const m = useMutation({
    mutationFn: () => call(api.POST("/api/v1/users", { body: f })),
    onSuccess: (u) => { qc.invalidateQueries({ queryKey: ["users"] }); qc.invalidateQueries({ queryKey: ["employees"] });
      toast("ok", `${u.full_name} can now sign in`); onClose(); setF(blank); },
    onError: (e) => { setErrors(fieldErrors(e)); toast("bad", e instanceof ApiError && e.problem.code === "CONFLICT" ? "This phone number is already registered" : e instanceof ApiError ? e.problem.detail : "Failed"); },
  });
  return (
    <Drawer title="Add user" open={open} onClose={onClose}>
      <div className="space-y-4">
        <Field label="Full name" error={errors.full_name}><input className="input" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
        <Field label="Phone (used to sign in)" error={errors.phone}><input className="input num" inputMode="numeric" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
        <Field label="First password" error={errors.password} hint="At least 8 characters. Ask them to change it after signing in.">
          <input className="input" type="text" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>
        <RolePicker enabled={open} value={f.roles} onChange={(roles) => setF({ ...f, roles })}
                    godownIds={f.godown_ids} onGodowns={(godown_ids) => setF({ ...f, godown_ids })} />
        <button className="btn-primary" disabled={!f.full_name || !f.phone || f.password.length < 8 || f.roles.length === 0 || m.isPending}
                onClick={() => m.mutate()}>Add user</button>
      </div>
    </Drawer>
  );
}

function EditUser({ user, onClose }: { user: User; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { user: me } = useAuth();
  const [roles, setRoles] = useState<string[]>(user.roles);
  const [godownIds, setGodownIds] = useState<string[]>(user.godown_ids);
  const save = useMutation({
    mutationFn: () => call(api.PUT("/api/v1/users/{user_id}/roles", { params: { path: { user_id: user.id } },
      body: { roles, godown_ids: roles.includes("GODOWN_SUPERVISOR") ? godownIds : [] } })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["users"] }); toast("ok", `Roles saved for ${user.full_name}`); onClose(); },
    onError: (e) => toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"),
  });
  const deactivate = useMutation({
    mutationFn: () => call(api.POST("/api/v1/users/{user_id}/deactivate", { params: { path: { user_id: user.id } } })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["users"] }); toast("ok", `${user.full_name} can no longer sign in`); onClose(); },
    onError: (e) => toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"),
  });
  const [pw, setPw] = useState("");
  const reset = useMutation({
    mutationFn: () => call(api.POST("/api/v1/users/{user_id}/password", { params: { path: { user_id: user.id } },
      body: { new_password: pw } })),
    onSuccess: () => { toast("ok", `New password set for ${user.full_name}. Tell them, and ask them to change it.`); setPw(""); },
    onError: (e) => toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"),
  });
  const activate = useMutation({
    mutationFn: () => call(api.POST("/api/v1/users/{user_id}/activate", { params: { path: { user_id: user.id } } })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["users"] }); toast("ok", `${user.full_name} can sign in again`); onClose(); },
    onError: (e) => toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"),
  });
  const isMe = me?.id === user.id;
  return (
    <Drawer title={user.full_name} open onClose={onClose}>
      <p className="num mb-4 text-[13px] text-steel">{user.phone}</p>
      <div className="space-y-4">
        <RolePicker enabled value={roles} onChange={setRoles} godownIds={godownIds} onGodowns={setGodownIds} />
        {roles.length === 0 && <p className="text-[13px] text-warn">With no role, this person cannot use this company.</p>}
        <button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>Save roles</button>
        {!isMe && (
          <div className="border-t border-seam pt-4">
            <Field label="Set a new password" hint="For someone who forgot theirs. Signs them out on every phone and unlocks the account.">
              <input className="input" type="text" autoComplete="off" value={pw} onChange={(e) => setPw(e.target.value)} />
            </Field>
            <button className="btn-quiet mt-2 h-9 border border-seam" disabled={pw.length < 8 || reset.isPending}
                    onClick={() => reset.mutate()}>Set password</button>
          </div>
        )}
        {!isMe && !user.is_active && (
          <div className="border-t border-seam pt-4">
            <button className="btn-primary" disabled={activate.isPending} onClick={() => activate.mutate()}>Reactivate user</button>
          </div>
        )}
        {!isMe && user.is_active && (
          <div className="border-t border-seam pt-4">
            <p className="mb-2 text-[13px] text-steel">Deactivating signs them out everywhere. Their history is kept.</p>
            <button className="btn-danger" disabled={deactivate.isPending}
                    onClick={() => { if (window.confirm(`Deactivate ${user.full_name}?`)) deactivate.mutate(); }}>Deactivate user</button>
          </div>
        )}
      </div>
    </Drawer>
  );
}
