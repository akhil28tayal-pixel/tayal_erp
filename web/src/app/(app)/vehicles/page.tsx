"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { Badge, Drawer, Empty, ErrorBox, Field, Loading, PageHeader, fieldErrors, fmtQty } from "@/components/ui";

const TYPES: Record<string, string> = {
  TRUCK_6W: "6-wheel truck", TRUCK_10W: "10-wheel truck", TRUCK_12W: "12-wheel truck", TRUCK_14W: "14-wheel truck",
  TRAILER: "Trailer", TRACTOR: "Tractor trolley", PICKUP: "Pickup", OTHER: "Other",
};
const STATUS_TONE: Record<string, "ok" | "warn" | "neutral"> = { AVAILABLE: "ok", ON_TRIP: "warn", MAINTENANCE: "warn", INACTIVE: "neutral" };

export default function VehiclesPage() {
  const { can } = useAuth();
  const manage = can("masters.transport_manage");
  const [addV, setAddV] = useState(false);
  const [addD, setAddD] = useState(false);
  const vehicles = useQuery({ queryKey: ["vehicles"], queryFn: () => call(api.GET("/api/v1/vehicles", { params: { query: { page_size: 200 } } })) });
  const drivers = useQuery({ queryKey: ["drivers"], queryFn: () => call(api.GET("/api/v1/drivers", { params: { query: { page_size: 200 } } })) });

  return (
    <>
      <PageHeader title="Vehicles & drivers" sub="Your own trucks. Capacity is checked when loads are planned."
                  actions={manage && <><button className="btn-quiet" onClick={() => setAddD(true)}>Add driver</button>
                    <button className="btn-primary" onClick={() => setAddV(true)}>Add vehicle</button></>} />
      <div className="grid gap-5 xl:grid-cols-[1fr_380px]">
        <div className="panel overflow-x-auto">
          {vehicles.error ? <ErrorBox error={vehicles.error} /> : vehicles.isLoading ? <Loading /> : vehicles.data!.items.length === 0 ? (
            <Empty title="No vehicles yet" hint={manage ? "Add each truck with its registration number and capacity." : undefined} />
          ) : (
            <table className="tbl">
              <thead><tr><th>Registration</th><th>Type</th><th className="text-right">Capacity</th><th>Status</th></tr></thead>
              <tbody>{vehicles.data!.items.map((v) => (
                <tr key={v.id}>
                  <td className="num font-semibold">{v.registration_no}</td><td>{TYPES[v.vehicle_type]}</td>
                  <td className="num text-right">{fmtQty(Number(v.capacity_kg) / 1000, 2)} MT</td>
                  <td><Badge tone={STATUS_TONE[v.status]}>{v.status.replace("_", " ").toLowerCase()}</Badge></td>
                </tr>))}
              </tbody>
            </table>
          )}
        </div>
        <div className="panel h-fit">
          <h2 className="display border-b border-seam px-4 py-3 text-[15px] font-bold">Drivers</h2>
          {drivers.isLoading ? <Loading /> : (drivers.data?.items.length ?? 0) === 0 ? <Empty title="No drivers yet" /> : (
            <ul className="divide-y divide-seam/70">
              {drivers.data!.items.map((d) => (
                <li key={d.id} className="flex justify-between px-4 py-2.5 text-[13.5px]">
                  <span className="font-semibold">{d.name}</span><a className="num text-steel hover:underline" href={`tel:${d.phone}`}>{d.phone}</a>
                </li>))}
            </ul>
          )}
        </div>
      </div>
      <AddVehicle open={addV} onClose={() => setAddV(false)} />
      <AddDriver open={addD} onClose={() => setAddD(false)} />
    </>
  );
}

function AddVehicle({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState({ registration_no: "", vehicle_type: "TRUCK_10W", capacity_mt: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const m = useMutation({
    mutationFn: () => call(api.POST("/api/v1/vehicles", { body: {
      registration_no: f.registration_no, vehicle_type: f.vehicle_type as "TRUCK_10W",
      capacity_kg: String(Number(f.capacity_mt) * 1000), ownership: "OWN", status: "AVAILABLE" } })),
    onSuccess: (v) => { qc.invalidateQueries({ queryKey: ["vehicles"] }); toast("ok", `${v.registration_no} added`); setF({ ...f, registration_no: "", capacity_mt: "" }); onClose(); },
    onError: (e) => { setErrors(fieldErrors(e)); toast("bad", e instanceof ApiError && e.problem.code === "CONFLICT" ? "This registration number already exists" : e instanceof ApiError ? e.problem.detail : "Failed"); },
  });
  return (
    <Drawer title="Add vehicle" open={open} onClose={onClose}>
      <div className="space-y-4">
        <Field label="Registration number" error={errors.registration_no} hint="Spaces and dashes are removed, e.g. DL1LAB1234">
          <input className="input num uppercase" value={f.registration_no} onChange={(e) => setF({ ...f, registration_no: e.target.value })} /></Field>
        <Field label="Type"><select className="input" value={f.vehicle_type} onChange={(e) => setF({ ...f, vehicle_type: e.target.value })}>
          {Object.entries(TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <Field label="Capacity (MT)" error={errors.capacity_kg}><input className="input num" inputMode="decimal" value={f.capacity_mt} onChange={(e) => setF({ ...f, capacity_mt: e.target.value })} /></Field>
        <button className="btn-primary" disabled={!f.registration_no || !Number(f.capacity_mt) || m.isPending} onClick={() => m.mutate()}>Add vehicle</button>
      </div>
    </Drawer>
  );
}

function AddDriver({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState({ name: "", phone: "", licence_no: "" });
  const m = useMutation({
    mutationFn: () => call(api.POST("/api/v1/drivers", { body: { name: f.name, phone: f.phone, licence_no: f.licence_no || null, is_active: true } })),
    onSuccess: (d) => { qc.invalidateQueries({ queryKey: ["drivers"] }); toast("ok", `${d.name} added`); setF({ name: "", phone: "", licence_no: "" }); onClose(); },
    onError: (e) => toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"),
  });
  return (
    <Drawer title="Add driver" open={open} onClose={onClose}>
      <div className="space-y-4">
        <Field label="Name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Phone"><input className="input num" inputMode="tel" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
        <Field label="Licence number (optional)"><input className="input" value={f.licence_no} onChange={(e) => setF({ ...f, licence_no: e.target.value })} /></Field>
        <button className="btn-primary" disabled={!f.name || !f.phone || m.isPending} onClick={() => m.mutate()}>Add driver</button>
      </div>
    </Drawer>
  );
}
