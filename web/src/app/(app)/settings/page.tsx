"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { ErrorBox, Loading, PageHeader } from "@/components/ui";

/** Plain-language names for the business rules (keys are the backend's). Unlisted keys show their raw name. */
const LABELS: Record<string, [string, string]> = {
  "credit.stale_policy": ["If Tally data is out of date", "HOLD sends the order to admin for approval"],
  "tally.stale_threshold_minutes": ["Tally data counts as out of date after (minutes)", ""],
  "credit.limit_source": ["Where credit limits come from", "ERP_ONLY: set on each dealer here"],
  "orders.auto_approve": ["Approve orders automatically when every check passes", ""],
  "orders.duplicate_window_minutes": ["Warn about a possible duplicate order within (minutes)", ""],
  "orders.auto_close_after_days": ["Close delivered orders after (days)", ""],
  "orders.default_tax_treatment": ["Default tax treatment", ""],
  "inventory.require_grn_confirmation": ["Godown must confirm receipts before stock counts", ""],
  "inventory.cement_age_alert_days": ["Flag cement older than (days)", ""],
  "inventory.self_approve_damage_bags": ["Supervisor can record damage without approval up to (bags)", ""],
  "dispatch.capacity_tolerance_pct": ["Allowed overload on a truck (%)", ""],
  "dispatch.pod_min_photos": ["Photos required at delivery", ""],
  "dispatch.pod_otp_enabled": ["Ask the customer for an OTP at delivery", ""],
  "collections.promise_grace_days": ["Grace days before a payment promise counts as broken", ""],
  "collections.promise_fulfil_threshold_pct": ["Promise is kept when this % is received", ""],
  "tally.url": ["Address of the Tally computer", "e.g. http://192.168.0.5:9000 — saved automatically by the first successful sync"],
  "credit.no_limit_policy": ["Dealers without a credit limit", "ALLOW: not credit-checked · HOLD: every order goes to admin"],
  "whatsapp.provider": ["How WhatsApp messages are sent", "CLICK_TO_CHAT: opens WhatsApp, you press send · CLOUD_API: sent automatically through Meta"],
  "whatsapp.template_name": ["Meta template name (automatic sending)", "As approved in WhatsApp Manager"],
  "whatsapp.template_language": ["Meta template language code", "e.g. en, en_US, hi — exactly as in WhatsApp Manager"],
  "whatsapp.template_params": ["What fills the template's {{1}}, {{2}}, ...", "In order, from: dealer, balance, as_of, sender"],
  "whatsapp.sender_name": ["Name dealers see at the end of the message", "Empty = the company name"],
  "whatsapp.balance_template": ["Balance message", "{dealer}, {balance}, {as_of} and {sender} are filled in. *text* is bold in WhatsApp."],
  "whatsapp.reminder_gap_hours": ["Ask before reminding the same dealer again within (hours)", ""],
  "whatsapp.delivery_template": ["WhatsApp to the dealer when a truck is delivered: template name", "Empty = off. See the box above for the text to get approved."],
  "tally.auto_sync_minutes": ["Sync with Tally automatically every (minutes)", "0 = off. Brings in balances, godowns, stock and sales approvals"],
  "tally.auto_sync_from": ["Automatic sync starts at", "24-hour time, e.g. 09:00"],
  "tally.auto_sync_to": ["Automatic sync stops at", "24-hour time, e.g. 20:30"],
};

export default function SettingsPage() {
  const { can } = useAuth();
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => call(api.GET("/api/v1/settings")) as Promise<Record<string, unknown>> });
  if (settings.error) return <ErrorBox error={settings.error} />;
  if (!settings.data) return <Loading />;
  const entries = Object.entries(settings.data).filter(([, v]) => typeof v !== "object");
  return (
    <>
      <PageHeader title="Business rules" sub={can("admin.settings") ? "Changes apply immediately and are recorded in the audit log." : "Only admins can change these."} />
      {can("tally.view") && <JobsCard admin={can("admin.settings")} />}
      {can("admin.settings") && <WhatsAppCard />}
      <div className="panel">
        <table className="tbl">
          <thead><tr><th>Rule</th><th className="w-72">Value</th></tr></thead>
          <tbody>{entries.map(([k, v]) => <Row key={k} k={k} v={v} editable={can("admin.settings")} />)}</tbody>
        </table>
      </div>
    </>
  );
}

function Row({ k, v, editable }: { k: string; v: unknown; editable: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [val, setVal] = useState(v == null ? "" : String(v));
  const long = k.endsWith("_template");
  const save = useMutation({
    mutationFn: (value: unknown) => call(api.PUT("/api/v1/settings/{key}", { params: { path: { key: k } }, body: { value } })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["settings"] }); toast("ok", "Rule saved"); },
    onError: (e) => { setVal(String(v)); toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"); },
  });
  const [label, hint] = LABELS[k] ?? [k, ""];
  return (
    <tr>
      <td><div>{label}</div>{hint && <div className="text-[12px] text-steel">{hint}</div>}</td>
      <td>
        {typeof v === "boolean" ? (
          <label className="text-[13.5px]"><input type="checkbox" disabled={!editable} checked={v}
            onChange={(e) => save.mutate(e.target.checked)} /> {v ? "Yes" : "No"}</label>
        ) : (
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); save.mutate(typeof v === "number" ? Number(val) : val); }}>
            {long ? <textarea className="input h-40 w-[26rem] max-w-full py-2 text-[13px]" aria-label={label} disabled={!editable} value={val} onChange={(e) => setVal(e.target.value)} />
              : <input className="input num h-8" aria-label={label} disabled={!editable} value={val} onChange={(e) => setVal(e.target.value)} />}
            {editable && val !== (v == null ? "" : String(v)) && <button className="btn-quiet h-8 self-start">Save</button>}
          </form>
        )}
      </td>
    </tr>
  );
}

/** Shows whether automatic WhatsApp (Meta Cloud API) is ready and sends a test message. Keys live in .env. */
function WhatsAppCard() {
  const toast = useToast();
  const qc = useQueryClient();
  const [num, setNum] = useState("");
  const st = useQuery({ queryKey: ["whatsapp-status"], queryFn: () => call(api.GET("/api/v1/collections/whatsapp")) });
  const test = useMutation({
    mutationFn: () => call(api.POST("/api/v1/collections/whatsapp/test", { body: { number: num } })),
    onSuccess: (r) => toast("ok", `Test message accepted by WhatsApp for +${r.number}`),
    onError: (e) => toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"),
  });
  const setProvider = useMutation({
    mutationFn: (value: string) => call(api.PUT("/api/v1/settings/{key}", { params: { path: { key: "whatsapp.provider" } }, body: { value } })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["whatsapp-status"] }); qc.invalidateQueries({ queryKey: ["settings"] }); qc.invalidateQueries({ queryKey: ["collections"] }); toast("ok", "Saved"); },
    onError: (e) => toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"),
  });
  const wa = useQuery({ queryKey: ["wa-orders", "status"], queryFn: () => call(api.GET("/api/v1/whatsapp-orders", { params: { query: { limit: 1 } } })) as Promise<{ ai_ready: boolean; webhook_ready: boolean }> });
  const s = st.data;
  if (!s) return null;
  const auto = s.provider === "CLOUD_API";
  return (
    <section className="panel mb-5 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="display text-[16px] font-bold">WhatsApp messages</h2>
          <p className="mt-0.5 text-[13.5px] text-steel">
            {auto ? "Automatic: balance reminders are sent from the server through Meta's WhatsApp API."
              : "Manual: the button opens WhatsApp with the message typed in and someone presses send."}
          </p>
        </div>
        <div className="flex gap-1 rounded-[6px] bg-concrete p-1" role="group" aria-label="Sending mode">
          {(["CLICK_TO_CHAT", "CLOUD_API"] as const).map((m) => (
            <button key={m} disabled={setProvider.isPending || (m === "CLOUD_API" && !s.api_keys_set)}
                    onClick={() => s.provider !== m && setProvider.mutate(m)}
                    className={`h-8 rounded-[5px] px-3 text-[13px] font-semibold disabled:opacity-40 ${s.provider === m ? "bg-blue text-white" : "text-steel"}`}>
              {m === "CLOUD_API" ? "Automatic (Meta API)" : "Manual (open WhatsApp)"}
            </button>))}
        </div>
      </div>
      <dl className="mt-4 grid gap-x-6 gap-y-1 text-[13.5px] sm:grid-cols-2">
        <div><dt className="inline text-steel">API keys in .env: </dt><dd className="inline font-semibold">{s.api_keys_set ? `set (phone number ID ${s.phone_number_id})` : "not set"}</dd></div>
        <div><dt className="inline text-steel">Template: </dt><dd className="num inline font-semibold">{s.template_name} ({s.template_language})</dd></div>
      </dl>
      <details className="mt-4 rounded-[6px] border border-seam px-4 py-3 text-[13.5px]">
        <summary className="cursor-pointer font-semibold">Delivery message to the dealer {s.delivery_template ? <span className="ml-1 text-ok">· on ({s.delivery_template})</span> : <span className="ml-1 text-steel">· off</span>}</summary>
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-steel">
          <li>In <b className="text-ink">WhatsApp Manager → Message templates → Create</b>, choose category <b className="text-ink">Utility</b>, name it e.g. <code className="text-ink">tayal_delivery_done</code>, language as your balance template.</li>
          <li>Body text (copy exactly):
            <pre className="mt-1 whitespace-pre-wrap rounded-[6px] bg-concrete p-2 text-[12.5px] text-ink">{DELIVERY_TEXT}</pre>
            Sample values: Sharma Building Material · 400 bag Ambuja PPC · DL1LA1234 · ITD/SO/2627/00142 · 16 Nov 2026, 04:30 PM · Raju</li>
          <li>When Meta approves it, enter its name under <b className="text-ink">“WhatsApp to the dealer when a truck is delivered”</b> in the rules below. From then on each delivered truck sends it automatically.</li>
        </ol>
      </details>
      <details className="mt-3 rounded-[6px] border border-seam px-4 py-3 text-[13.5px]">
        <summary className="cursor-pointer font-semibold">Orders sent to the business number {wa.data?.webhook_ready
          ? <span className="ml-1 text-ok">· connected on this server</span> : <span className="ml-1 text-steel">· not connected</span>}
          {wa.data && <span className={`ml-2 ${wa.data.ai_ready ? "text-ok" : "text-warn"}`}>· AI reader {wa.data.ai_ready ? "on" : "off"}</span>}</summary>
        <p className="mt-2 text-steel">Dealers send orders to the business WhatsApp number; each one is read into a draft order on <b className="text-ink">WhatsApp orders</b>. Staff can also paste messages there without any of this.</p>
        <ol className="mt-2 list-decimal space-y-1.5 pl-5 text-steel">
          <li>In the <code className="text-ink">.env</code> file add <code className="text-ink">ERP_ANTHROPIC_API_KEY</code> (console.anthropic.com → API keys), <code className="text-ink">ERP_WHATSAPP_VERIFY_TOKEN</code> (any long secret you make up) and <code className="text-ink">ERP_WHATSAPP_APP_SECRET</code> (Meta app → App settings → Basic → App secret). Restart ./start-mac.sh.</li>
          <li>Running on the Windows PC with Cloudflare Tunnel (https://erp.tayaltraders.co.in)? Skip this step: the address is already public. On the Mac, let Meta reach only the webhook, over HTTPS, with Tailscale:
            <pre className="mt-1 whitespace-pre-wrap rounded-[6px] bg-concrete p-2 text-[12px] text-ink">tailscale funnel --bg --set-path /api/v1/whatsapp/webhook http://127.0.0.1:8000/api/v1/whatsapp/webhook</pre>
            It prints an https://….ts.net address. The rest of the ERP stays private.</li>
          <li>Meta app → WhatsApp → Configuration → Webhook → Edit: Callback URL = <code className="text-ink">https://erp.tayaltraders.co.in/api/v1/whatsapp/webhook</code> (or the Tailscale address + <code className="text-ink">/api/v1/whatsapp/webhook</code>), Verify token = the one from step 1 → Verify and save. Then under Webhook fields subscribe to <b className="text-ink">messages</b>.</li>
          <li>Send a test order from another phone to the business number; it should appear on WhatsApp orders within seconds. The computer running the ERP must be on.</li>
        </ol>
      </details>
      {s.api_keys_set ? (
        <form className="mt-4 flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); test.mutate(); }}>
          <input className="input num h-9 w-52" inputMode="tel" placeholder="Your mobile number" aria-label="Test mobile number" value={num} onChange={(e) => setNum(e.target.value)} />
          <button className="btn-quiet h-9" disabled={!num.trim() || test.isPending}>{test.isPending ? "Sending…" : "Send test message"}</button>
          <span className="text-[12.5px] text-steel">Sends the balance template with sample values ("Test Dealer", ₹1,23,456, today's date).</span>
        </form>
      ) : (
        <p className="mt-3 text-[13px] text-warn">Add ERP_WHATSAPP_TOKEN and ERP_WHATSAPP_PHONE_NUMBER_ID to the .env file in the tayal_erp folder, then restart ./start-mac.sh.</p>
      )}
    </section>
  );
}

const DELIVERY_TEXT = "Dear {{1}}, your delivery has reached: {{2}}. Truck {{3}}, order {{4}}, delivered on {{5}}, received by {{6}}. Thank you for your business.";

type Job = { status: string; started_at: string; finished_at: string | null; detail: string | null; file: string | null; size_bytes: number | null };
const when = (iso: string) => new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/** What runs by itself: the automatic Tally sync and the nightly backup. */
function JobsCard({ admin }: { admin: boolean }) {
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["jobs"], refetchInterval: (x) => (x.state.data?.backup.latest?.status === "RUNNING" ? 3000 : 60_000),
    queryFn: () => call(api.GET("/api/v1/jobs")) as Promise<{ scheduler_on: boolean; auto_sync: { every_minutes: number; from: string; to: string };
      backup: { folder: string; time: string; keep_days: number; latest: Job | null; last_success: Job | null } }>,
  });
  const backup = useMutation({
    mutationFn: () => call(api.POST("/api/v1/jobs/backup")),
    onSuccess: () => { toast("ok", "Backup started"); setTimeout(() => qc.invalidateQueries({ queryKey: ["jobs"] }), 1500); },
    onError: (e) => toast("bad", e instanceof ApiError ? e.problem.detail : "Failed"),
  });
  const j = q.data;
  if (!j) return null;
  const b = j.backup, last = b.last_success, latest = b.latest;
  const old = !last || Date.now() - new Date(last.started_at).getTime() > 36 * 3600e3;
  return (
    <section className="panel mb-5 grid gap-5 p-5 md:grid-cols-2">
      <div>
        <h2 className="display text-[16px] font-bold">Automatic Tally sync</h2>
        <p className="mt-1 text-[13.5px]">
          {!j.scheduler_on ? <span className="text-warn">Timed jobs are switched off on this server.</span>
            : j.auto_sync.every_minutes > 0 ? <>Every <b>{j.auto_sync.every_minutes} minutes</b> from <b>{j.auto_sync.from}</b> to <b>{j.auto_sync.to}</b>: dealer balances, godowns, products, godown stock, delivered orders to Tally and their approvals.</>
            : <span className="text-steel">Off. Set the minutes below to switch it on.</span>}
        </p>
        <p className="mt-1 text-[12.5px] text-steel">Needs Tally open on its computer. The sync card on the Overview shows each run (“Automatic”).</p>
      </div>
      <div>
        <div className="flex items-start justify-between gap-3">
          <h2 className="display text-[16px] font-bold">Nightly backup</h2>
          {admin && <button className="btn-quiet h-8" disabled={backup.isPending || latest?.status === "RUNNING"} onClick={() => backup.mutate()}>
            {latest?.status === "RUNNING" ? "Backing up…" : "Back up now"}</button>}
        </div>
        <p className={`mt-1 text-[13.5px] ${old ? "font-semibold text-bad" : ""}`}>
          {last ? <>Last backup <b>{when(last.started_at)}</b>{last.size_bytes ? ` · ${(last.size_bytes / 1e6).toFixed(1)} MB` : ""}</> : "No backup yet"}
          {old && last ? " — more than a day old" : ""}</p>
        {latest?.status === "FAILED" && <p className="mt-1 text-[12.5px] text-bad">Last attempt failed: {latest.detail}</p>}
        <p className="mt-1 text-[12.5px] text-steel">Every night at {b.time} (or as soon as the server is next on), into <code className="text-ink">{b.folder}</code>, keeping {b.keep_days} days.
          Copy that folder to Google Drive or another disk now and then; change it with ERP_BACKUP_DIR in .env.</p>
      </div>
    </section>
  );
}
