"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useDebounced } from "@/lib/hooks";
import { Empty, ErrorBox, Loading, PageHeader, Pager, fmtMoney } from "@/components/ui";
import { WhatsAppBalanceButton } from "@/components/whatsapp";
import { fmtTime } from "@/components/orders";
import { TallySyncCard } from "@/components/tally-sync";
import { ContactDrawer, useCanEditContacts } from "@/components/contacts";
import { WhatsAppBulkButton } from "@/components/whatsapp-bulk";

const ago = (iso: string) => {
  const h = (Date.now() - new Date(iso).getTime()) / 36e5;
  return h < 1 ? "just now" : h < 24 ? `${Math.floor(h)} h ago` : `${Math.floor(h / 24)} d ago`;
};
const showNumber = (n: string) => `+${n.slice(0, 2)} ${n.slice(2, 7)} ${n.slice(7)}`;

export default function CollectionsPage() {
  const { can } = useAuth();
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [dues, setDues] = useState(true);
  const [pick, setPick] = useState<Record<string, string>>({});
  const [adding, setAdding] = useState<{ id: string; name: string } | null>(null);
  const canEditContacts = useCanEditContacts();
  const dq = useDebounced(q);
  const summary = useQuery({ queryKey: ["collections", "summary"], queryFn: () => call(api.GET("/api/v1/collections/summary")) });
  const rows = useQuery({
    queryKey: ["collections", "balances", dq, page, dues],
    queryFn: () => call(api.GET("/api/v1/collections/balances", { params: { query: { q: dq || undefined, page, page_size: 50, dues_only: dues } } })),
  });
  const s = summary.data;
  const stale = s?.last_sync ? Date.now() - new Date(s.last_sync).getTime() > 6 * 36e5 : true;

  return (
    <>
      <PageHeader title="Collections" sub="Dealer balances from Tally. Send a dealer their balance on WhatsApp with one click." />
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <Tile label="Total outstanding" value={fmtMoney(s?.total_due)} />
        <Tile label="Dealers with dues" value={s ? String(s.dealers_with_dues) : "—"} />
        {can("tally.view") ? <TallySyncCard compact /> : <Tile label="Balances as of" value={s?.last_sync ? fmtTime(s.last_sync) : "Never synced"}
              note={stale ? "Balances may be out of date" : undefined} />}
      </div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <input className="input h-9 max-w-xs" placeholder="Search dealer" aria-label="Search dealer"
               value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        <label className="flex items-center gap-2 text-[13.5px]">
          <input type="checkbox" checked={dues} onChange={(e) => { setDues(e.target.checked); setPage(1); }} /> Only dealers who owe
        </label>
        {can("collections.followup") && <div className="ml-auto flex items-center gap-2"><WhatsAppBulkButton automatic={s?.whatsapp_provider === "CLOUD_API"} /></div>}
      </div>
      <div className="panel overflow-x-auto">
        {rows.error ? <ErrorBox error={rows.error} /> : rows.isLoading ? <Loading /> : rows.data!.items.length === 0 ? (
          <Empty title={dq ? "No dealer matches" : "No balances yet"} hint="Balances arrive with the Tally sync." />
        ) : (
          <>
            <table className="tbl">
              <thead><tr><th>Dealer</th><th className="text-right">Balance</th><th>Mobile</th><th>Last reminder</th><th /></tr></thead>
              <tbody>{rows.data!.items.map((d) => {
                const bal = Number(d.tally_balance ?? 0);
                const chosen = pick[d.dealer_id] ?? d.numbers[0]?.number;
                return (
                  <tr key={d.dealer_id}>
                    <td><Link href={`/dealers/${d.dealer_id}`} className="font-semibold hover:underline">{d.name}</Link>
                      <div className="num text-[12px] text-steel">{d.code}{d.status !== "ACTIVE" && <span className="ml-1 text-bad">· {d.status.toLowerCase()}</span>}</div></td>
                    <td className="num text-right font-semibold">{d.tally_balance === null ? "—" : <>{fmtMoney(Math.abs(bal))} <span className="text-[11.5px] font-normal text-steel">{bal >= 0 ? "Dr" : "Cr"}</span></>}</td>
                    <td className="num text-[13px]">
                      {d.numbers.length === 0 ? (canEditContacts
                        ? <button className="text-warn underline" onClick={() => setAdding({ id: d.dealer_id, name: d.name })}>Add mobile</button>
                        : <span className="text-steel">No mobile</span>)
                        : d.numbers.length === 1 ? <span title={d.numbers[0].label}>{showNumber(d.numbers[0].number)}</span>
                        : <select className="input h-8 py-0 text-[13px]" aria-label={`Mobile for ${d.name}`} value={chosen}
                                  onChange={(e) => setPick({ ...pick, [d.dealer_id]: e.target.value })}>
                            {d.numbers.map((n) => <option key={n.number} value={n.number}>{showNumber(n.number)} · {n.label}</option>)}
                          </select>}
                    </td>
                    <td className="text-[12.5px] text-steel">{d.last_reminder_at
                      ? <span title={fmtTime(d.last_reminder_at)}>{ago(d.last_reminder_at)}{d.last_reminder_by ? ` · ${d.last_reminder_by}` : ""}</span> : "—"}</td>
                    <td className="text-right">{can("collections.followup") && bal > 0 &&
                      <WhatsAppBalanceButton compact dealerId={d.dealer_id} dealerName={d.name} number={chosen} disabled={!chosen} />}</td>
                  </tr>);
              })}</tbody>
            </table>
            <Pager page={page} pageSize={50} total={rows.data!.total} onPage={setPage} />
          </>
        )}
      </div>
      {adding && <ContactDrawer open dealerId={adding.id} dealerName={adding.name} onClose={() => setAdding(null)} />}
      <p className="mt-3 text-[12.5px] text-steel">
        {s?.whatsapp_provider === "CLOUD_API"
          ? "WhatsApp messages are sent automatically from your business number. Every reminder is logged."
          : "The button opens WhatsApp on this computer or phone with the message typed in — check it and press send. Every reminder is logged."}
      </p>
    </>
  );
}

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="panel px-4 py-3">
      <div className="text-[12.5px] text-steel">{label}</div>
      <div className="num mt-0.5 text-[20px] font-bold">{value}</div>
      {note && <div className="text-[12px] text-warn">{note}</div>}
    </div>
  );
}
