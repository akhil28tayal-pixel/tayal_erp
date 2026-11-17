"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";

const when = (iso: string) => new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const ago = (iso: string) => {
  const m = (Date.now() - new Date(iso).getTime()) / 6e4;
  return m < 1 ? "just now" : m < 60 ? `${Math.floor(m)} min ago` : m < 1440 ? `${Math.floor(m / 60)} h ago` : `${Math.floor(m / 1440)} d ago`;
};

type Step = { key: string; label: string; status: "WAITING" | "RUNNING" | "OK" | "FAILED" | "SKIPPED"; detail: string };

/** "Sync with Tally" — one button brings everything up to date: dealers (balances, mobile numbers), godowns and
 *  products, godown stock, delivered orders still to go to Tally, and approvals done in Tally. Runs on the server in
 *  the background; this card shows each step as it goes and then refreshes every page's data. */
export function TallySyncCard({ compact = false }: { compact?: boolean }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [url, setUrl] = useState("");
  const [askUrl, setAskUrl] = useState(false);
  const status = useQuery({
    queryKey: ["tally-sync"], enabled: can("tally.view"),
    queryFn: () => call(api.GET("/api/v1/tally/sync")),
    refetchInterval: (q) => (q.state.data?.latest?.status === "RUNNING" ? 2000 : false),
  });
  const latest = status.data?.latest;
  const running = latest?.status === "RUNNING";
  const lastSeen = useRef<string | null>(null);

  // When a run we watched finishes, tell the user and refresh everything that came from Tally.
  useEffect(() => {
    if (!latest) return;
    if (lastSeen.current === latest.id + "RUNNING" && latest.status !== "RUNNING") {
      if (latest.status === "SUCCESS") {
        if (latest.error) toast("bad", latest.error);
        else toast("ok", "Everything is up to date with Tally");
        qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "tally-sync" });
      } else toast("bad", latest.error ?? "Tally sync failed");
    }
    lastSeen.current = latest.id + latest.status;
  }, [latest, qc, toast]);

  const start = useMutation({
    mutationFn: (tally_url: string | null) => call(api.POST("/api/v1/tally/sync", { body: { tally_url } })),
    onSuccess: (run) => { lastSeen.current = run.id + "RUNNING"; setAskUrl(false); qc.invalidateQueries({ queryKey: ["tally-sync"] }); },
    onError: (e) => {
      if (e instanceof ApiError && e.problem.code === "TALLY_URL_REQUIRED") { setAskUrl(true); return; }
      toast("bad", e instanceof ApiError ? e.problem.detail : "Could not start the sync");
    },
  });

  if (!can("tally.view")) return null;
  const ok = status.data?.last_success;
  const steps = (latest?.summary?.steps ?? []) as Step[];
  const canSync = can("tally.sync");
  // No saved address yet (nothing has synced): always show the address box, filled with the last attempt.
  const click = () => {
    if (status.data?.tally_url) start.mutate(null);
    else { setUrl(url || latest?.tally_url?.replace(/^http:\/\//, "") || ""); setAskUrl(true); }
  };

  return (
    <section className={`panel ${compact ? "px-4 py-3" : "mb-5 flex flex-wrap items-center gap-4 px-5 py-4"}`}>
      <div className="min-w-0 flex-1">
        <div className="text-[12.5px] text-steel">Tally sync</div>
        <div className="font-semibold">
          {running ? "Syncing with Tally…" : ok?.finished_at ? `Last synced ${ago(ok.finished_at)}` : "Never synced"}
        </div>
        <div className="text-[12.5px] text-steel">
          {running ? "Bringing everything up to date. This takes a minute or two."
            : ok?.finished_at ? `${when(ok.finished_at)}${ok.triggered_by_name ? ` · ${ok.triggered_by_name}` : ""}`
            : "Dealer balances, godowns, products, godown stock and sales approvals — all in one go"}
        </div>
        {latest?.status === "FAILED" && !running && steps.length === 0 && (
          <div className="mt-1 text-[12.5px] text-bad">Last attempt failed ({when(latest.started_at)}): {latest.error}</div>
        )}
        {steps.length > 0 && (
          <ul className={`mt-2.5 grid gap-x-6 gap-y-1.5 text-[13px] ${compact ? "" : "sm:grid-cols-2"}`} aria-label="Sync steps">
            {steps.map((x) => <StepRow key={x.key} step={x} />)}
          </ul>
        )}
      </div>
      {canSync && (askUrl ? (
        <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (url.trim()) start.mutate(url.trim()); }}>
          <input className="input num h-9 w-56" autoFocus aria-label="Tally computer address" placeholder="Tally PC address, e.g. 192.168.0.5"
                 value={url} onChange={(e) => setUrl(e.target.value)} />
          <button className="btn-primary h-9" disabled={!url.trim() || start.isPending}>Sync</button>
          <button type="button" className="btn-quiet h-9" onClick={() => setAskUrl(false)}>Cancel</button>
          <p className="w-full text-[12px] text-steel">Tally must be open on that computer with the HTTP port on (F1 → Settings → Connectivity, port 9000).</p>
        </form>
      ) : (
        <div className={`flex items-center gap-2 ${compact ? "mt-2" : ""}`}>
          <button className="btn-primary h-9" disabled={running || start.isPending} onClick={click}>
            {running || start.isPending ? <><span className="mr-2 inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />Syncing…</> : "Sync with Tally"}
          </button>
          {!compact && status.data?.tally_url && !running && (
            <button className="text-[12px] text-steel underline" onClick={() => { setUrl(status.data!.tally_url); setAskUrl(true); }}>Change Tally address</button>
          )}
        </div>
      ))}
    </section>
  );
}

function StepRow({ step }: { step: Step }) {
  const icon = {
    OK: <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-ok text-[12px] font-bold text-white">✓</span>,
    FAILED: <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-bad text-[12px] font-bold text-white">!</span>,
    RUNNING: <span className="h-5 w-5 shrink-0 animate-spin rounded-full border-2 border-blue/25 border-t-blue" />,
    WAITING: <span className="h-5 w-5 shrink-0 rounded-full border-2 border-seam" />,
    SKIPPED: <span className="h-5 w-5 shrink-0 rounded-full border-2 border-seam" />,
  }[step.status];
  return (
    <li className="flex min-w-0 items-start gap-2">
      {icon}
      <div className="min-w-0">
        <div className={step.status === "WAITING" || step.status === "SKIPPED" ? "text-steel" : "font-semibold"}>{step.label}</div>
        {step.detail && <div className={`text-[12px] ${step.status === "FAILED" ? "text-bad" : "text-steel"}`}>{step.detail}</div>}
      </div>
    </li>
  );
}
