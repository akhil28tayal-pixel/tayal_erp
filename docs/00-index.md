# Tayal ERP — Phase 1 Design Pack

Cement & TMT distribution ERP: **Order → Validation → Reservation → Godown Allocation → Dispatch → Loading → Delivery → Payment Follow-up.**

Status: **Step 1 (Architecture & Planning) — draft for review.** No code yet. The repository was empty when this was written, so the design is greenfield.

| # | Document | Covers (prompt section) |
|---|----------|-------------------------|
| 01 | [Architecture](01-architecture.md) | A — system architecture, key decisions & trade-offs |
| 02 | [Data model](02-data-model.md) | B — schema, ERD, quantity/UoM model, inventory ledger |
| 03 | [Modules & screens](03-modules-and-screens.md) | C, D, E — feature inventory, web nav, Android nav per role |
| 04 | [API contracts](04-api-contracts.md) | F — REST conventions and endpoints |
| 05 | [Tally integration](05-tally-integration.md) | G — Windows connector, sync, reconciliation |
| 06 | [Workflows & state machines](06-workflows.md) | H — order / inventory / dispatch / collection flows, Workflows A–F |
| 07 | [Security & permissions](07-security-permissions.md) | I — auth, RBAC matrix, data scoping |
| 08 | [Development plan](08-development-plan.md) | J — staged backlog, dependencies, acceptance criteria |
| 09 | [Open questions & assumptions](09-open-questions.md) | Business questions that change the design |

## Design principles (applies everywhere)

1. **Backend is the only authority.** Web and Android are thin clients over one API; no business rule lives only in a client.
2. **Ledgers, not mutable balances.** Stock and money are append-only ledgers; balance tables are projections that can be rebuilt and are checked against the ledger.
3. **Commands, not field edits.** Status changes happen through explicit action endpoints (`POST /orders/{id}/approve`), each checked against a state machine and a permission.
4. **Original + normalized quantities.** Every quantity stores what the user typed (value + unit) and the normalized base quantity, so rounding never drifts.
5. **Tally is the accounting truth in Phase 1.** The ERP reads from Tally; it never invents receivables. Field-reported payments stay "reported" until matched to a Tally receipt.
6. **Company boundaries are explicit.** Every business row carries `company_id`; C&F (principal-owned) stock never mixes with own trading stock.
7. **Simple and reliable over clever.** A modular monolith, one PostgreSQL database, Celery for background work.
