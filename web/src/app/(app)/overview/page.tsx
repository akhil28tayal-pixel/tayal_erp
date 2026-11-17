"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api, call } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { PageHeader, fmtMoney } from "@/components/ui";
import { TallySyncCard } from "@/components/tally-sync";

/** Until orders/stock/collections exist, the overview shows real setup status: what has been brought in from
 *  Tally and what still needs doing before go-live. Every line links to the page that fixes it. */
type Tile = { label: string; value: React.ReactNode; href: string; note?: string; tone?: "warn" | "bad" };

/** The four stages of an order as big coloured tiles (same as the phone's home screen), a big Book Order button,
 *  then the smaller numbers: delivered today, sales waiting in Tally, outstanding. */
const STAGES = [
  { label: "Waiting for approval", tab: "review", status: ["UNDER_REVIEW", "ON_HOLD"], icon: "✋",
    cls: "bg-gradient-to-br from-[#1B4FD8] to-[#3D72F2] text-white" },
  { label: "Ready to send", tab: "ready", status: ["APPROVED", "PARTIALLY_DISPATCHED"], icon: "📦",
    cls: "bg-gradient-to-br from-[#0D2A72] to-[#1C428F] text-white" },
  { label: "On the road", tab: "road", status: ["DISPATCHED", "PARTIALLY_DELIVERED"], icon: "🚚",
    cls: "bg-gradient-to-br from-[#0B7DD6] to-[#3AA8F5] text-white" },
  { label: "Delivered", tab: "done", status: ["DELIVERED", "CLOSED"], days: 30, icon: "✓",
    cls: "border-2 border-blue bg-gradient-to-br from-white to-blue-tint text-blue-dark" },
] as const;

function TodayTiles() {
  const { can } = useAuth();
  const orders = can("orders.view"), dues = can("collections.view"), tallyOn = can("tally.view"), rep = can("reports.view");
  const since = new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10);
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  const counts = useQuery({
    queryKey: ["ov-stages"], enabled: orders, refetchInterval: 60_000,
    queryFn: () => Promise.all(STAGES.map((st) => call(api.GET("/api/v1/orders", { params: { query: {
      page_size: 1, status: [...st.status], date_from: "days" in st ? since : undefined } } })).then((r) => r.total))),
  });
  const tally = useQuery({ queryKey: ["tally-sales", "counts"], enabled: tallyOn, refetchInterval: 60_000,
    queryFn: () => call(api.GET("/api/v1/tally/sales", { params: { query: { status: "NONE" } } })) });
  const money = useQuery({ queryKey: ["collections", "summary"], enabled: dues,
    queryFn: () => call(api.GET("/api/v1/collections/summary")) });
  const waQ = useQuery({ queryKey: ["wa-orders", "counts"], enabled: orders, refetchInterval: 30_000,
    queryFn: () => call(api.GET("/api/v1/whatsapp-orders", { params: { query: { limit: 1 } } })) as Promise<{ counts: Record<string, number> }> });
  const sold = useQuery({ queryKey: ["ov-today-sales", today], enabled: rep, refetchInterval: 120_000,
    queryFn: () => call(api.GET("/api/v1/reports/sales", { params: { query: { date_from: today, date_to: today, group: "day" } } })) as Promise<{ total: { bags: string; tonnes: string; amount: string; trucks: number } }> });

  const small: Tile[] = [];
  const waOpen = waQ.data ? (waQ.data.counts.DRAFTED ?? 0) + (waQ.data.counts.NEEDS_HELP ?? 0) + (waQ.data.counts.FAILED ?? 0) : 0;
  if (waOpen > 0) small.push({ label: "WhatsApp orders to check", value: waOpen, href: "/whatsapp-orders", note: "Read from dealers' messages", tone: "warn" });
  if (rep && sold.data) {
    const t = sold.data.total;
    small.push({ label: "Delivered today", value: `${Number(t.bags).toLocaleString("en-IN")} bags`, href: "/reports",
                 note: `${t.tonnes} t · ${shortMoney(Number(t.amount))} · ${t.trucks} truck${t.trucks === 1 ? "" : "s"}` });
  }
  if (tallyOn && tally.data) {
    const w = tally.data.counts.WAITING_APPROVAL ?? 0, bad = (tally.data.counts.FAILED ?? 0) + (tally.data.counts.REJECTED ?? 0);
    small.push({ label: "Sales waiting in Tally", value: w, href: "/tally-sales",
                 note: bad ? `${bad} not sent or rejected` : w ? "Admin to approve in Tally" : "All approved",
                 tone: bad ? "warn" : undefined });
  }
  if (dues && money.data) {
    const due = Number(money.data.total_due);
    small.push({ label: "Outstanding (Tally)", value: shortMoney(due), href: "/collections",
                 note: `${fmtMoney(String(Math.round(due)))} · ${money.data.dealers_with_dues} dealers owe money` });
  }
  if (!orders && !small.length) return null;
  return (
    <>
      {orders && (
        <div className="mb-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
          {STAGES.map((st, i) => {
            const n = counts.data?.[i];
            return (
              <Link key={st.tab} href={`/orders?tab=${st.tab}`}
                    className={`group flex min-h-[148px] flex-col justify-between rounded-[14px] p-5 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md ${st.cls}`}>
                <div className="flex items-start justify-between gap-2">
                  <span className="text-[17px] font-semibold leading-tight">{st.label}</span>
                  <span aria-hidden className="text-[22px] opacity-80">{st.icon}</span>
                </div>
                <div>
                  <div className="num display text-[44px] font-bold leading-none">{counts.isLoading ? "…" : n ?? "—"}</div>
                  <div className="mt-1 text-[13px] opacity-80">{"days" in st ? "last 30 days" : n ? "Tap to open" : "None right now"}</div>
                </div>
              </Link>);
          })}
        </div>)}
      {can("orders.create") && (
        <Link href="/orders/new" className="mb-5 flex h-16 items-center justify-center gap-3 rounded-[14px] bg-blue text-[20px] font-bold text-white shadow-sm hover:bg-blue-dark">
          <span aria-hidden className="text-[26px] leading-none">＋</span> Book Order
        </Link>)}
      {small.length > 0 && (
        <div className={`mb-5 grid gap-3 md:grid-cols-3 ${small.length > 3 ? "xl:grid-cols-4" : ""}`}>
          {small.map((t) => (
            <Link key={t.label} href={t.href} className="panel block min-w-0 overflow-hidden px-5 py-4 hover:border-blue">
              <div className="text-[13.5px] font-semibold text-steel">{t.label}</div>
              <div className="num display mt-1 truncate text-[30px] font-bold leading-none" title={String(t.value)}>{t.value}</div>
              {t.note && <div className={`mt-1.5 text-[12.5px] ${t.tone === "warn" ? "font-semibold text-warn" : "text-steel"}`}>{t.note}</div>}
            </Link>))}
        </div>)}
    </>
  );
}

/** Big money in short Indian form for a small tile: ₹3.10 Cr, ₹12.4 L, ₹85,000. */
function shortMoney(v: number) {
  const a = Math.abs(v), sign = v < 0 ? "-" : "";
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(1)} L`;
  return `${sign}₹${Math.round(a).toLocaleString("en-IN")}`;
}

export default function Overview() {
  const { user, company } = useAuth();
  const q = (path: "/api/v1/dealers" | "/api/v1/godowns" | "/api/v1/products" | "/api/v1/vehicles",
             query: Record<string, unknown> = {}) =>
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    call(api.GET(path as any, { params: { query: { page_size: 1, ...query } } } as any)) as Promise<{ total: number }>;

  const dealers = useQuery({ queryKey: ["ov", "dealers"], queryFn: () => q("/api/v1/dealers") });
  const godowns = useQuery({ queryKey: ["ov", "godowns"], queryFn: () => q("/api/v1/godowns", { erp_enabled: true }) });
  const allGodowns = useQuery({ queryKey: ["ov", "godowns-all"], queryFn: () => q("/api/v1/godowns") });
  const products = useQuery({ queryKey: ["ov", "products"], queryFn: () => q("/api/v1/products", { erp_enabled: true }) });
  const allProducts = useQuery({ queryKey: ["ov", "products-all"], queryFn: () => q("/api/v1/products") });
  const setup = useQuery({ queryKey: ["ov", "products-setup"], queryFn: () => q("/api/v1/products", { needs_setup: true }) });
  const vehicles = useQuery({ queryKey: ["ov", "vehicles"], queryFn: () => q("/api/v1/vehicles") });

  const n = (x: { data?: { total: number }; isLoading: boolean }) => (x.isLoading ? "…" : x.data?.total ?? "—");
  const rows: { label: string; value: React.ReactNode; href: string; todo?: string }[] = [
    { label: "Customers from Tally", value: n(dealers), href: "/dealers",
      todo: dealers.data?.total === 0 ? "Run the Tally sync to bring in Sundry Debtors" : undefined },
    { label: "Godowns in use", value: <>{n(godowns)} <span className="text-steel">of {n(allGodowns)} in Tally</span></>, href: "/godowns",
      todo: allGodowns.data?.total === 0 ? "Run the Tally sync to bring in godowns" : undefined },
    { label: "Products in use", value: <>{n(products)} <span className="text-steel">of {n(allProducts)} in Tally</span></>, href: "/products",
      todo: allProducts.data?.total === 0 ? "Run the Tally sync to bring in stock items"
        : setup.data?.total ? `${setup.data.total} need setup (brand, type or size)` : undefined },
    { label: "Vehicles", value: n(vehicles), href: "/vehicles",
      todo: vehicles.data?.total === 0 ? "Add your trucks with their capacity" : undefined },
  ];

  return (
    <>
      <PageHeader title={`Good ${greeting()}, ${user?.full_name.split(" ")[0] ?? ""}`}
                  sub={`${company?.company_name}: today's work, then the state of the data from Tally.`} />
      <TodayTiles />
      <TallySyncCard />
      <div className="panel">
        <table className="tbl">
          <thead><tr><th>Master data</th><th className="w-48">Count</th><th>Next step</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label}>
                <td><Link className="font-semibold hover:underline" href={r.href}>{r.label}</Link></td>
                <td className="num">{r.value}</td>
                <td className={r.todo ? "text-warn" : "text-steel"}>{r.todo ?? "Ready"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "morning" : h < 17 ? "afternoon" : "evening";
}
