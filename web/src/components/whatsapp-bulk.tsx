"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, call } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import { useToast } from "@/components/toast";
import { Drawer, Field, fmtMoney } from "@/components/ui";

/** "Send to all on WhatsApp": preview who gets the balance message, then send in the background with progress. */
export function WhatsAppBulkButton({ automatic }: { automatic: boolean }) {
  const [open, setOpen] = useState(false);
  const latest = useQuery({
    queryKey: ["whatsapp-bulk"], queryFn: () => call(api.GET("/api/v1/collections/whatsapp/bulk")),
    refetchInterval: (q) => (q.state.data?.status === "RUNNING" ? 1500 : false),
  });
  const run = latest.data;
  const running = run?.status === "RUNNING";
  return (
    <>
      <button className="inline-flex h-9 items-center gap-2 rounded-[6px] bg-[#1f8f4e] px-3 text-[13.5px] font-semibold text-white hover:bg-[#187a42] disabled:opacity-40"
              disabled={!automatic} onClick={() => setOpen(true)}
              title={automatic ? "Send the balance to every dealer who owes" : "Turn on automatic sending in Settings first"}>
        {running ? `Sending… ${run!.sent + run!.failed}/${run!.total}` : "Send to all on WhatsApp"}
      </button>
      {!automatic && <Link href="/settings" className="text-[12.5px] text-steel underline">Turn on automatic sending</Link>}
      <BulkDrawer open={open} onClose={() => setOpen(false)} run={run ?? null} />
    </>
  );
}

type Run = NonNullable<Awaited<ReturnType<typeof fetchRun>>>;
const fetchRun = () => call(api.GET("/api/v1/collections/whatsapp/bulk"));

function BulkDrawer({ open, onClose, run }: { open: boolean; onClose: () => void; run: Run | null }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [minBalance, setMinBalance] = useState("0");
  const [skipRecent, setSkipRecent] = useState(true);
  const [includeBlocked, setIncludeBlocked] = useState(false);
  const [startedId, setStartedId] = useState<string | null>(null);
  const body = { min_balance: minBalance.trim() || "0", skip_recent: skipRecent, include_blocked: includeBlocked };
  const dBody = useDebounced(JSON.stringify(body), 300);
  const preview = useQuery({
    queryKey: ["whatsapp-bulk-preview", dBody], enabled: open && !startedId, retry: false,
    queryFn: () => call(api.POST("/api/v1/collections/whatsapp/bulk/preview", { body: JSON.parse(dBody) })),
  });
  const start = useMutation({
    mutationFn: () => call(api.POST("/api/v1/collections/whatsapp/bulk", { body })),
    onSuccess: (r) => { setStartedId(r.id); qc.setQueryData(["whatsapp-bulk"], r); qc.invalidateQueries({ queryKey: ["whatsapp-bulk"] }); },
    onError: (e) => toast("bad", e instanceof ApiError ? e.problem.detail : "Could not start"),
  });
  // Tell the user when the run they started finishes, and refresh the "last reminder" column.
  const announced = useRef<string | null>(null);
  useEffect(() => {
    if (run && run.id === startedId && run.status !== "RUNNING" && announced.current !== run.id) {
      announced.current = run.id;
      qc.invalidateQueries({ queryKey: ["collections"] });
      qc.removeQueries({ queryKey: ["whatsapp-bulk-preview"] });  // counts changed: recheck next time
      toast(run.failed ? "bad" : "ok", `WhatsApp: ${run.sent} sent${run.failed ? `, ${run.failed} failed` : ""}`);
    }
  }, [run, startedId, qc, toast]);
  useEffect(() => { if (open && (!run || run.status !== "RUNNING")) setStartedId(null); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const showing = run && (run.status === "RUNNING" || run.id === startedId) ? run : null;
  const p = preview.data;
  return (
    <Drawer title="Send balance to all dealers" open={open} onClose={onClose}>
      {showing ? (
        <div className="space-y-4 text-[13.5px]">
          <p className="font-semibold">{showing.status === "RUNNING" ? "Sending…" : showing.status === "DONE" ? "Finished" : "Stopped"}</p>
          <div className="h-2 rounded bg-concrete"><div className="h-2 rounded bg-[#1f8f4e] transition-all"
               style={{ width: `${showing.total ? ((showing.sent + showing.failed) / showing.total) * 100 : 0}%` }} /></div>
          <p className="num">{showing.sent} sent · {showing.failed} failed · {showing.total} in total</p>
          {showing.status === "RUNNING" && <p className="text-steel">You can close this panel; sending carries on.</p>}
          {showing.error && <p className="text-bad">{showing.error}</p>}
          {showing.failures.length > 0 && (
            <div>
              <p className="mb-1 font-semibold">Not sent</p>
              <ul className="max-h-72 space-y-1 overflow-y-auto text-[12.5px]">
                {showing.failures.map((f, i) => <li key={i}><b>{String(f.dealer)}</b> — <span className="text-steel">{String(f.error)}</span></li>)}
              </ul>
            </div>
          )}
          {showing.status !== "RUNNING" && <button className="btn-quiet" onClick={() => { qc.removeQueries({ queryKey: ["whatsapp-bulk-preview"] }); setStartedId(null); }}>Back</button>}
        </div>
      ) : (
        <div className="space-y-4 text-[13.5px]">
          <Field label="Only dealers who owe at least (₹)" hint="0 = everyone with a balance due">
            <input className="input num" inputMode="numeric" value={minBalance} onChange={(e) => setMinBalance(e.target.value)} />
          </Field>
          <label className="flex items-start gap-2">
            <input type="checkbox" className="mt-1" checked={skipRecent} onChange={(e) => setSkipRecent(e.target.checked)} />
            <span>Skip dealers already reminded in the last 24 hours</span>
          </label>
          <label className="flex items-start gap-2">
            <input type="checkbox" className="mt-1" checked={includeBlocked} onChange={(e) => setIncludeBlocked(e.target.checked)} />
            <span>Include blocked dealers (e.g. doubtful debts)</span>
          </label>
          <div className="rounded-[6px] border border-seam p-3">
            {preview.error ? <p className="text-bad">{preview.error instanceof ApiError ? preview.error.problem.detail : "Could not check"}</p> : !p ? <p className="text-steel">Checking…</p> : (
              <>
                <p className="text-[15px]"><b className="num">{p.to_send}</b> dealers will get the message <span className="num text-steel">(₹{fmtMoney(p.to_send_amount).replace("₹", "")} due)</span></p>
                <ul className="mt-2 space-y-0.5 text-[12.5px] text-steel">
                  {p.no_mobile > 0 && <li>{p.no_mobile} skipped: no mobile number</li>}
                  {p.recently_reminded > 0 && <li>{p.recently_reminded} skipped: reminded in the last 24 hours</li>}
                  {p.blocked > 0 && <li>{p.blocked} skipped: blocked</li>}
                </ul>
                {p.sample.length > 0 && <p className="mt-2 text-[12.5px] text-steel">First: {p.sample.slice(0, 5).map((s) => s.name).join(", ")}{p.to_send > 5 ? "…" : ""}</p>}
              </>
            )}
          </div>
          <button className="btn-primary" disabled={!p || p.to_send === 0 || start.isPending || run?.status === "RUNNING"}
                  onClick={() => { if (window.confirm(`Send the balance message on WhatsApp to ${p!.to_send} dealers now?`)) start.mutate(); }}>
            {run?.status === "RUNNING" ? "Another send is running" : `Send to ${p?.to_send ?? 0} dealers`}
          </button>
          <p className="text-[12px] text-steel">Uses your approved template. Meta charges about ₹0.12 per message.</p>
        </div>
      )}
    </Drawer>
  );
}
