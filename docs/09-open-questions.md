# Decisions, Open Questions & Assumptions

## Decisions (confirmed by Akhil, 2026-11-09)

| # | Topic | Decision | Design consequence |
|---|-------|----------|--------------------|
| D1 | Companies | **One company: Ishika Traders Daily** (the Tally company of the same name) | One ERP company is seeded and linked to that Tally company. `company_id` stays on every table, so another company can be added later without migration. The books are non-GST, so orders default to `tax_treatment = NON_GST`. |
| D2 | JSW C&F app | **No relation** | No C&F stock, no SAP import, no commission claims. The ownership dimension stays in the schema (required by the brief), but only one owner, *Ishika Traders Daily – own stock*, is seeded. |
| D3 | Tally direction | **Read-only for now** | The connector only exports from Tally. Delivery orders get `invoice_ref` by matching the synced sales voucher (or by typing it in). |
| D4 | Credit hold | **Held orders are approved only by admin** | `credit.override` is granted only to **Super Admin** and **Business Owner**. The Ops Manager can see a hold but cannot release it. A stale Tally balance also leads to a hold. |
| D5 | Customers & rates | **Customers come from Tally; rates are customer-specific** | Dealers are created by the Tally sync from Sundry Debtors ledgers (no manual creation in the ERP). The ERP adds only operational data: addresses/sites, contacts, sales/collection assignment, credit settings. Rates live in `dealer_rates` (dealer × product, optionally × godown). When there is no ERP rate, the default is the **last rate billed to that customer for that item in Tally**. Any change from the authorised rate needs approval. |
| D6 | Direct dispatch | **A direct vehicle is allocated to the "Direct" godown and the material moves straight to delivery** | `Direct` is a godown of kind `DIRECT`. Allocating a direct vehicle to an order receives the vehicle's quantity into Direct, reserves it for that order and issues it on dispatch in one go. The stock is never "available" for other orders. |

## Decisions (2026-11-09, second round)

| # | Topic | Decision | Design consequence |
|---|-------|----------|--------------------|
| D7 | Tally PC | **The Windows connector can be installed** on the Tally machine | Step 6 builds the connector as a Windows service. Until then, `sync-tally-masters` pulls customers and godowns directly over the network. |
| D8 | Trucks | **All trucks are owned** | Vehicles default to `OWN` under one "Own fleet" transporter. Freight is tracked as the trip's own cost (diesel, driver, toll), not as a transporter bill. |
| D9 | Credit limit | **Set in the ERP** | `credit.limit_source = ERP_ONLY`. Tally's limit is imported for reference only. |
| D10 | Godowns | **Fetched from Tally, and unused ones can be switched off in the ERP** | Godowns are created/renamed only by the Tally sync (no manual create). `erp_enabled` switch per godown, which a re-sync never overrides. A godown deleted in Tally is flagged and switched off, never deleted. || D11 | Products | **Fetched from Tally (stock items), with an ERP on/off switch like godowns** | Brand, cement/TMT, bag weight and TMT diameter are read from the item name where clear (Ambuja, Shree/Bangur, Rockstrong, JSW, Wonder, Kamdhenu; "12mm"; "20kg"). Anything unclear arrives switched off as **needs setup** and cannot be ordered until completed on the Products page. Products seeded earlier link to their Tally item by name instead of duplicating. |
| D12 | Tally data rules | Ledgers under **Doubtful Debts** come in **blocked**; Driver ledgers stay customers; truck-number "godowns" (DL01…, HR38…) come in switched off | A manual unblock is never undone by a sync. || D13 | TMT brand codes | In Tally item names: **KD = Kamdhenu, JD = Jindal, BL = Birla, R = Rungta, RT = Rathi, SS = Shyam Steel** | "TMT 12MM (KD)" imports as Kamdhenu 12 mm. The IS 1786 kg/m chart now applies to every TMT brand (category level); a brand's own chart can be added to override it. |
| D14 | Other building materials | **Built now, switched on next phase** | New product type *Other material* (GENERAL): blocks, bricks, dust/reta/rodi, AAC, POP, chemicals, Zero M, wire, nails… stocked and sold in their Tally unit (PC, CUM, FT, LTR, PKT, NOS, rickshaw load, kg). The setting `products.other_materials_enabled` (off) keeps them from being switched on; next phase flips it. |
| D15 | Orders before inventory | **Orders and dispatch go live before the stock ledger (Step 4)** | An order line names the godown to load from; dispatch is checked against the order (never more than ordered, never more than loaded), not against a stock figure. When Step 4 lands, approval also reserves stock and dispatch issues it — no change to how people book or dispatch. |
| D16 | Credit check | **Tally ledger balance + approved-but-not-sent orders vs the ERP credit limit** | The ledger sync now also reads each customer's closing balance. Exposure = Tally balance + value of approved orders not yet dispatched (dispatched material is assumed billed in Tally). Over the limit → **on hold for admin**; balance older than `tally.stale_threshold_minutes` → on hold (`credit.stale_policy`). Dealers with no ERP limit are not credit-checked (`credit.no_limit_policy = ALLOW`). Overdue-days checks wait for bill-wise outstanding (Step 6). |
| D17 | Who does what on an order | Sales books; Operations approves and plans trucks; only Super Admin / Business Owner release credit holds and approve below-rate prices; nobody approves their own order unless they hold `orders.self_approval` | A Sales + Operations person books orders and fills in dispatch details, but another person approves their orders. |

## Still open

None blocking. Data still needed: Kamdhenu's weight chart (kg/m per diameter) and bundle sizes.

## Assumptions built into the design

1. Cement is stocked in **bags** (50 kg; 20 kg packs at 50 bags/MT where applicable). TMT is stocked in **kg**, with pieces/bundles as secondary counts.
2. Dealer names on sheets differ from Tally ledger names, so the ERP keeps aliases.
3. The interface is in English.
4. Data is hosted in India (AWS Mumbai) when it moves to the cloud.
5. No e-way bill or e-invoice generation in Phase 1.
