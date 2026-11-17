# I. Security & Permissions

## 1. Authentication
- Login by phone + password (Argon2id hashes). Lockout after 5 failures for 15 min. Optional TOTP for Super Admin / Owner / Accounts.
- Access token: JWT (EdDSA), 15 min, contains `sub`, `sid`, token version — **no permissions inside**; permissions are loaded per request from a Redis-cached map invalidated on role change.
- Refresh token: opaque random 256-bit, stored hashed, 30-day expiry, **rotated on every use**; reuse of an old token revokes the whole family (stolen-token detection). Web: refresh token in `HttpOnly; Secure; SameSite=Strict` cookie scoped to `/api/v1/auth`. Android: flutter_secure_storage (Keystore).
- Deactivating a user revokes all sessions immediately.

## 2. Authorization model

**Permission check = role permission ∧ data scope.**

1. **Permission** — fine-grained codes (`module.action`), granted to roles, roles granted to users **per company**.
2. **Company scope** — every request carries `X-Company-Id`; the user must hold a role in it. All repository queries filter by it (enforced by a scoped query helper; an isolation test suite tries every list/detail endpoint across companies).
3. **Godown scope** — users with `scope.godown_restricted` (Godown Supervisor) see/act only on `user_godown_scopes` godowns.
4. **Dealer scope** — Sales/Collection Executives see only dealers currently assigned to them (`dealer_assignments` open rows) unless they hold `dealers.view_all`.
5. **Own-record scope** — Drivers see only DOs on trips assigned to their `drivers.user_id`.

Frontends hide menu items using `/auth/me` permissions, **but that is cosmetic**; every endpoint re-checks.

## 3. Permission catalogue (excerpt)

```
orders.view  orders.create  orders.approve  orders.hold  orders.cancel  orders.cancel_approved
orders.revise  orders.rate_override  orders.close  orders.export  orders.self_approval
credit.view  credit.override  credit.override_unlimited
inventory.view  inventory.receive  inventory.grn_confirm  inventory.import  inventory.adjust
inventory.adjust_approve  inventory.transfer_request  inventory.transfer_approve
inventory.transfer_dispatch  inventory.transfer_receive  inventory.count  inventory.negative_override
dispatch.view  dispatch.plan  dispatch.load  dispatch.confirm  dispatch.capacity_override
dispatch.freight_manage  dispatch.freight_approve  dispatch.exception_manage
delivery.update_status  delivery.pod
collections.view  collections.view_all  collections.followup  collections.promise
collections.assign  collections.priority_override  collections.targets
payments.report  payments.verify  payments.reconcile  payments.unreconcile
tally.view  tally.sync  tally.map  tally.connector_admin
masters.dealers_manage  masters.products_manage  masters.prices_manage  masters.prices_approve
masters.godowns_manage  masters.transport_manage
admin.users  admin.roles  admin.settings  admin.audit_view
reports.view  reports.financial
```

## 4. Role × capability matrix

✓ = allowed · A = allowed, subject to approval policy · S = scoped (assigned dealers / godowns / own trips) · — = no

| Capability | Super Admin | Owner | Ops Mgr | Accounts | Sales Exec | Coll. Exec | Godown Sup. | Driver |
|---|---|---|---|---|---|---|---|---|
| Users, roles, settings | ✓ | ✓ | — | — | — | — | — | — |
| Masters: dealers | ✓ | ✓ | ✓ | ✓ (credit fields) | view S | view S | — | — |
| Masters: products, prices | ✓ | ✓ | A (prices) | view | view | — | view | — |
| Create / submit order | ✓ | ✓ | ✓ | — | S | — | — | — |
| Approve order | ✓ | ✓ | ✓ | — | — | — | — | — |
| Rate below minimum | ✓ | ✓ | A | — | A (request) | — | — | — |
| Credit override / release credit hold (D4: admin only) | ✓ | ✓ | — (view only) | — | request | — | — | — |
| Cancel after approval | ✓ | ✓ | A | — | request | — | — | — |
| View dealer outstanding | ✓ | ✓ | ✓ | ✓ | S (if `credit.view`) | S | — | — |
| Stock view | ✓ | ✓ | ✓ | ✓ | availability only | — | S | — |
| Goods receipt / GRN confirm | ✓ | — | ✓ | — | — | — | S | — |
| Excel stock import | ✓ | — | ✓ | ✓ | — | — | — | — |
| Stock adjustment raise / approve | ✓ | approve | raise + approve A | — | — | — | raise S | — |
| Transfer request / approve | ✓ | approve | ✓ | — | — | — | request/dispatch/receive S | — |
| Plan dispatch, trips, vehicles | ✓ | view | ✓ | — | — | — | view S | — |
| Loading, gate pass, dispatch confirm | ✓ | — | ✓ | — | — | — | S | — |
| Delivery status, POD | ✓ | — | ✓ | — | — | — | — | S |
| Freight approve additional | ✓ | ✓ | A | — | — | — | — | — |
| Follow-ups, promises | ✓ | ✓ | view | ✓ | — | S | — | — |
| Report payment | ✓ | ✓ | — | ✓ | — | S | — | — |
| Verify / reconcile payment | ✓ | ✓ | — | ✓ | — | — | — | — |
| Assign collection dealers | ✓ | ✓ | — | ✓ | — | — | — | — |
| Priority override | ✓ | ✓ | — | ✓ | — | — | — | — |
| Tally sync / mapping | ✓ | view | — | ✓ | — | — | — | — |
| Reports (financial) | ✓ | ✓ | ops only | ✓ | own | own | godown | — |
| Audit log | ✓ | ✓ | — | — | — | — | — | — |

Roles are data (seeded defaults); the matrix above is the seed and `seed-base` keeps system roles exactly in line with it.

**Sales = orders only** (decision 2026-11-09): book orders for assigned dealers, with read access to dealers, their balance, products and stock. **Roles combine**: a user given *Sales* and *Operations* can book orders and plan dispatch (vehicle, driver, loading). Super Admin assigns roles on the web *Users* page; nobody can remove their own user-admin access, and nobody can grant permissions they do not hold (the *scope* restrictions of Godown Supervisor/Driver don't count as power).

## 5. Approval policies (configurable, seeded defaults)

| Approval type | Default rule |
|---|---|
| Credit override (incl. stale-Tally hold) | Super Admin or Business Owner only (decision D4) |
| Rate below `min_rate` | ≤ 1 % below → Ops Mgr; more → Owner |
| Cancel after approval | Ops Mgr |
| Material order revision | Ops Mgr |
| Stock adjustment | Damage ≤ 10 bags / 200 kg by Godown Sup. self-approved with photo; else Ops Mgr; value > ₹50k → Owner |
| Additional freight | ≤ ₹1,000 Ops Mgr; above → Owner |
| Payment unreconcile | Accounts lead / Owner |
| Negative stock (authorised process) | Owner only, time-boxed flag on the balance |

(Thresholds are placeholders — see open question Q5.)

## 6. Data protection
- TLS everywhere; HSTS; strict CORS (web origin only); CSRF not applicable to bearer APIs, cookie refresh endpoint uses SameSite=Strict + origin check.
- Attachments: private bucket, server-side encryption, presigned URLs valid 5 min, content-type & size whitelist (images ≤ 10 MB, PDF/XLSX ≤ 20 MB), SHA-256 stored, filenames never trusted.
- Secrets only from environment / AWS Secrets Manager; `.env.example` committed, `.env` git-ignored.
- PII minimal (names, phones, addresses). Phone numbers masked in exports for roles without `dealers.view_contact`.
- Input validation via Pydantic with strict types; SQL only through SQLAlchemy parameters.
- Audit log immutable; DB app role lacks UPDATE/DELETE on `audit_logs` and `inventory_movements`.
- Backups: nightly `pg_dump` + WAL/PITR (RDS 14 days), S3 versioning; monthly restore drill documented.
- Rate limiting (Redis) on auth and connector endpoints; structured security logs (logins, failures, token reuse, permission denials).
