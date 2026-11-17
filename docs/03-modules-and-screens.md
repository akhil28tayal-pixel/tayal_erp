# C, D, E. Feature & Screen Inventory, Web Navigation, Android Navigation

Legend: **W** = web dashboard, **A** = Android app. Every list screen has search, filters, sort, pagination, saved filters, Excel export (permission-gated), empty/loading/error states.

## C. Module-wise features and screens

### Shared / masters
| Screen | W | A | Notes |
|--------|---|---|-------|
| Login, OTP/password reset, change password | ✓ | ✓ | Phone + password; optional TOTP for admins |
| Company switcher | ✓ | ✓ | Only companies user has roles in; "All companies" view for Owner |
| Dealers list / detail (tabs: profile, contacts, addresses, rates, orders, outstanding, follow-ups, assignments history, Tally mapping) | ✓ | ✓ (read, assigned only) | |
| Products & brands, UoM conversions (effective-dated) | ✓ | — | Conversion tester ("25 MT = ? bags") |
| Price lists (draft → approve → active) | ✓ | — | Excel import |
| Godowns, inventory owners, owner sale rules | ✓ | — | |
| Transporters, vehicles, drivers, freight rate cards | ✓ | — | |
| Employees, users, roles, permissions, scopes | ✓ | — | |
| Notifications centre | ✓ | ✓ | Push on Android |
| Approvals inbox (all approval types, one queue) | ✓ | ✓ (managers) | Approve/reject with note |
| Attachments viewer | ✓ | ✓ | Presigned URLs |
| Audit trail (per entity tab + global search) | ✓ | — | |
| Settings (per company) | ✓ | — | Credit policy, stale threshold, aging buckets, priority weights, numbering, approval thresholds |

### Module 1 — Orders
| Screen | W | A |
|--------|---|---|
| Order dashboard (KPIs: today, pending approval, approved, awaiting allocation, pending dispatch, partially dispatched, delivered, cancelled; qty by brand; dealer-wise; exec-wise) | ✓ | summary cards |
| Orders list (filters: date, dealer, brand, godown, status, company, employee, source, priority) | ✓ | ✓ (own/assigned) |
| New / edit order — dealer picker shows outstanding, overdue, available credit, last 5 orders, last rate; line entry in any allowed unit with live conversion; repeat-order shortcut | ✓ | ✓ (offline draft) |
| Order detail — lines with ordered / allocated / dispatched / delivered / cancelled, credit check panel, allocation panel, DOs, revisions diff, status timeline, attachments | ✓ | ✓ |
| Review & approve — validation results, godown suggestion editor (split across godowns), credit override form | ✓ | ✓ (approve/reject only) |
| Revise / partial cancel / cancel with reason | ✓ | limited |

### Module 2 — Collections
| Screen | W | A |
|--------|---|---|
| Collection dashboard (total, overdue, today, MTD, target vs actual, promises due today, broken, high-risk, exec-wise, aging distribution, outstanding movement, collection effectiveness index) | ✓ | exec summary |
| Outstanding list (dealer-wise: ledger balance vs invoice-wise, overdue, buckets, priority, exec) | ✓ | ✓ (assigned) |
| Dealer statement (bill-wise with allocations, receipts, notes) + PDF/Excel | ✓ | ✓ |
| Aging report (due-date / bill-date toggle, configurable buckets) | ✓ | — |
| My day (executive): today's follow-ups, promises due, broken promises, recent payments, disputes | ✓ | ✓ (home) |
| Log follow-up (outcome-driven form → creates promise / dispute / next date) | ✓ | ✓ (offline draft, click-to-call logs call) |
| Promises list / detail with matched receipts | ✓ | ✓ |
| Report payment (amount, mode, UTR/cheque, bank, proof photo) | ✓ | ✓ |
| Payment verification queue (Accounts) | ✓ | — |
| Reconciliation workbench (verified reports ↔ Tally receipts, suggested matches) | ✓ | — |
| Assignments (bulk assign/reassign dealers, history) | ✓ | — |
| Collection targets | ✓ | — |
| Priority list with factors + override | ✓ | ✓ (read) |

### Module 3 — Inventory
| Screen | W | A |
|--------|---|---|
| Inventory dashboard (by brand, by godown, available/reserved/in-transit/damaged, low stock, aging, discrepancies) | ✓ | supervisor summary |
| Stock position grid (product × godown, owner filter, drill to batches) | ✓ | ✓ (assigned godown) |
| Stock ledger (movement history with running balance) | ✓ | ✓ |
| Goods receipt (manual) / GRN confirmation of pending receipts | ✓ | ✓ (confirm with photos) |
| Excel import wizard (upload, mapping templates, preview, validate, post) + import history | ✓ | — |
| Reservations view (by order / by godown) | ✓ | — |
| Transfers list / new / approve / dispatch / receive | ✓ | ✓ (dispatch & receive) |
| Adjustments (new, approve, post) with attachments | ✓ | ✓ (raise damage) |
| Stock count (create, enter counts, variance, raise adjustment) | ✓ | ✓ (count entry) |
| Cement aging report; TMT weight variance report | ✓ | — |

### Module 4 — Dispatch
| Screen | W | A |
|--------|---|---|
| Dispatch dashboard (today's plan, MT dispatched, pending deliveries, vehicles in transit, loading queue, exceptions, godown-wise, transporter-wise, avg turnaround, freight) | ✓ | — |
| Planning board (kanban: awaiting allocation → ready → vehicle assigned → loading → loaded → dispatched → in transit → delivered; exception badge; drag = command call) | ✓ | — |
| Create DO(s) from order (split by godown / date) | ✓ | — |
| Trip planner (group DOs on a vehicle, capacity bar, freight estimate) | ✓ | — |
| Loading queue / loading entry (actual qty, shortage, damage, photos) → loading slip & gate pass PDF | ✓ | ✓ (supervisor) |
| Dispatch confirm | ✓ | ✓ |
| My trips (driver): stops, navigate (opens Maps), call customer, status updates, POD capture (signature, photos, OTP) | — | ✓ |
| DO detail with event timeline, POD, exceptions, freight | ✓ | ✓ |
| Exceptions list / detail | ✓ | ✓ |
| Freight (rate cards, trip freight, additional charge approval, variance) | ✓ | — |

### Reports (basic, Phase 1)
Daily sales/order register, dispatch register, delivery register with POD status, stock summary & ledger, cement aging, TMT variance, outstanding & aging, collection register, promise performance, exec performance, freight register. All exportable to Excel; heavy ones generated in background and delivered as a notification link.

---

## D. Web dashboard navigation

```
┌ Top bar: company switcher · global search (order no, dealer, DO, vehicle) · approvals bell · notifications · user menu
├ 1  Overview           Owner dashboard (KPI tiles → filtered pages), exceptions feed
├ 2  Orders             Dashboard · All orders · New order · Pending approval · Backorders
├ 3  Dispatch           Dashboard · Planning board · Delivery orders · Trips · Loading queue · Exceptions · Freight
├ 4  Collections        Dashboard · Outstanding · Aging · Follow-ups · Promises · Payments (verify) · Reconciliation · Assignments · Targets
├ 5  Inventory          Dashboard · Stock position · Stock ledger · Receipts · Imports · Transfers · Adjustments · Stock counts
├ 6  Dealers            List · Dealer detail (tabs)
├ 7  Godowns            List · Godown detail (stock, supervisors, dispatch stats)
├ 8  Reports            Report catalogue (by module) · My generated reports
├ 9  Approvals          Inbox · My requests · History
├ 10 Integrations       Tally: connectors, companies, ledger mapping, sync jobs, errors, reconciliation
└ 11 Settings           Company · Users & roles · Products & brands · UoM conversions · Price lists · Transporters & vehicles · Owners & sale rules · Business rules · Numbering
```

Owner dashboard tiles (each a link to a filtered list): Today's orders · Today's dispatch (MT) · Today's collection · Total outstanding · Overdue · Pending approvals · Pending deliveries · Inventory by godown (stacked table, not a decorative chart) · Brand-wise sales (MTD vs last month) · Employee performance (orders booked, collected vs target) · Operational exceptions (open by age).

Keyboard: `/` search, `N` new (context), `J/K` row navigation, `Enter` open, `A` approve in review screens (with confirm). URL holds all filter state so filtered views are shareable and saveable.

---

## E. Android navigation per role

Bottom navigation is built from the user's permissions in the selected company; users with several roles see a merged set (max 5 tabs, rest under "More").

| Role | Home | Tabs |
|------|------|------|
| **Sales Executive** | Today's orders · New order button · pending orders · recent deliveries · alerts (holds/rejections) | Home · Orders · Dealers (assigned; outstanding visible if permitted) · New order · More |
| **Collection Executive** | Today's follow-ups (sorted by priority) · promises due · overdue dealers · MTD vs target | Home · Follow-ups · Promises · Record payment · Dealers |
| **Godown Supervisor** | Pending loading · pending GRNs · transfers to dispatch/receive · damaged stock · stock snapshot | Home · Loading · Stock · Transfers · More (GRN, adjustments, counts) |
| **Driver** | Assigned trips today, ordered stops | Trips · Trip detail (address, call, navigate, status buttons, POD) · History |
| **Ops Manager / Owner** | Approvals count, today's KPIs, exceptions | Home · Approvals · Orders · Dispatch · Collections |

**Offline behaviour**

| Action | Offline? | On sync |
|--------|---------|--------|
| Order draft | Saved locally | Submitted → full server validation; may come back ON_HOLD / error, shown in "Needs attention" |
| Follow-up / promise | Queued | Created with client UUID (idempotent) |
| Payment report + proof photo | Queued | Duplicate UTR check server-side |
| Loading quantities, dispatch confirm | **Online only** (stock movement) | — |
| Driver status / POD | Queued with device timestamp | Validated (delivered ≤ dispatched) |
| Stock lookups, dealer lists | Cached read-only, with "as of" time | Refresh on open |

Every queued item shows a status chip: *Saved on phone · Sending · Synced · Needs attention*. Local DB encrypted (SQLCipher, key in Android Keystore via flutter_secure_storage); cache cleared on logout.
