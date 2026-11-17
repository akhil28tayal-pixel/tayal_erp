"""Permission catalogue and the seeded default role matrix (docs/07-security-permissions.md).

Roles are data: the seed command syncs these defaults, and a Super Admin can edit them later.
"""

PERMISSIONS: dict[str, str] = {
    # orders
    "orders.view": "View orders",
    "orders.create": "Create, edit drafts and submit orders",
    "orders.approve": "Approve or reject orders",
    "orders.hold": "Put orders on hold / release non-credit holds",
    "orders.cancel": "Cancel orders before approval",
    "orders.cancel_approved": "Cancel approved orders (approval request)",
    "orders.revise": "Revise submitted/approved orders",
    "orders.rate_override": "Sell below the authorised dealer rate",
    "orders.close": "Close delivered orders",
    "orders.export": "Export orders",
    "orders.self_approval": "Approve own orders",
    # credit
    "credit.view": "See dealer outstanding / credit position",
    "credit.override": "Release credit holds (admin only)",
    # inventory
    "inventory.view": "View stock",
    "inventory.receive": "Create goods receipts",
    "inventory.grn_confirm": "Confirm goods receipts",
    "inventory.import": "Import stock / purchase files",
    "inventory.adjust": "Raise stock adjustments",
    "inventory.adjust_approve": "Approve stock adjustments",
    "inventory.transfer_request": "Request godown transfers",
    "inventory.transfer_approve": "Approve godown transfers",
    "inventory.transfer_dispatch": "Dispatch transfers",
    "inventory.transfer_receive": "Receive transfers",
    "inventory.count": "Enter stock counts",
    "inventory.negative_override": "Authorise negative stock",
    # dispatch
    "dispatch.view": "View dispatch",
    "dispatch.plan": "Plan delivery orders and trips",
    "dispatch.load": "Record loading",
    "dispatch.confirm": "Confirm dispatch / gate pass",
    "dispatch.capacity_override": "Override vehicle capacity",
    "dispatch.freight_manage": "Manage freight",
    "dispatch.freight_approve": "Approve additional freight",
    "dispatch.exception_manage": "Manage delivery exceptions",
    "delivery.update_status": "Update delivery status",
    "delivery.pod": "Capture proof of delivery",
    # collections
    "collections.view": "View collections for assigned dealers",
    "collections.view_all": "View collections for all dealers",
    "collections.followup": "Log follow-ups",
    "collections.promise": "Record payment promises",
    "collections.assign": "Assign dealers to executives",
    "collections.priority_override": "Override priority scores",
    "collections.targets": "Manage collection targets",
    "payments.report": "Report payments",
    "payments.verify": "Verify reported payments",
    "payments.reconcile": "Reconcile payments with Tally",
    "payments.unreconcile": "Undo a reconciliation",
    # tally
    "tally.view": "View Tally sync status",
    "tally.sync": "Trigger Tally sync",
    "tally.map": "Map Tally ledgers",
    "tally.connector_admin": "Register/rotate Tally connectors",
    # masters
    "dealers.view_all": "See all dealers (not only assigned)",
    "dealers.view_contact": "See dealer phone numbers in exports",
    "masters.view": "View master data",
    "masters.dealers_manage": "Edit dealer operational data",
    "masters.products_manage": "Manage brands, products, conversions",
    "masters.prices_manage": "Manage dealer rates",
    "masters.prices_approve": "Approve dealer rates",
    "masters.godowns_manage": "Manage godowns and stock owners",
    "masters.transport_manage": "Manage transporters, vehicles, drivers",
    # admin
    "admin.users": "Manage users, employees and roles",
    "admin.roles": "Edit role permissions",
    "admin.settings": "Edit company settings",
    "admin.audit_view": "View audit log",
    # reports
    "reports.view": "Run operational reports",
    "reports.financial": "Run financial reports",
    # scope markers
    "scope.godown_restricted": "Restricted to assigned godowns",
    "scope.own_trips_only": "Restricted to own delivery trips",
}

ALL = set(PERMISSIONS)

ROLE_DEFAULTS: dict[str, tuple[str, set[str]]] = {
    "SUPER_ADMIN": ("Super Admin", ALL - {"scope.godown_restricted", "scope.own_trips_only"}),
    "BUSINESS_OWNER": (
        "Business Owner",
        {
            p
            for p in ALL
            if not p.startswith("scope.")
            and p not in {"tally.connector_admin", "admin.roles", "payments.unreconcile"}
        },
    ),
    "OPERATIONS_MANAGER": (
        "Operations Manager",
        {
            "orders.view", "orders.create", "orders.approve", "orders.hold", "orders.cancel",
            "orders.cancel_approved", "orders.revise", "orders.close", "orders.export", "credit.view",
            "inventory.view", "inventory.receive", "inventory.grn_confirm", "inventory.import",
            "inventory.adjust", "inventory.adjust_approve", "inventory.transfer_request",
            "inventory.transfer_approve", "inventory.transfer_dispatch", "inventory.transfer_receive",
            "inventory.count", "dispatch.view", "dispatch.plan", "dispatch.load", "dispatch.confirm",
            "dispatch.capacity_override", "dispatch.freight_manage", "dispatch.exception_manage",
            "delivery.update_status", "delivery.pod", "dealers.view_all", "masters.view",
            "masters.prices_manage", "masters.godowns_manage", "masters.transport_manage", "reports.view",
        },
    ),
    "ACCOUNTS_EXECUTIVE": (
        "Accounts Executive",
        {
            "orders.view", "credit.view", "inventory.view", "inventory.import", "collections.view",
            "collections.view_all", "collections.followup", "collections.promise", "collections.assign",
            "collections.priority_override", "collections.targets", "payments.report", "payments.verify",
            "payments.reconcile", "tally.view", "tally.sync", "tally.map", "dealers.view_all",
            "dealers.view_contact", "masters.view", "masters.dealers_manage", "reports.view",
            "reports.financial",
        },
    ),
    "SALES_EXECUTIVE": (
        "Sales Executive",
        # Books orders only. The view permissions are what order entry needs: pick an assigned dealer, see
        # their balance, pick a product and check stock. No payments, dispatch or approvals.
        {"orders.view", "orders.create", "credit.view", "inventory.view", "masters.view"},
    ),
    "COLLECTION_EXECUTIVE": (
        "Collection Executive",
        {
            "collections.view", "collections.followup", "collections.promise", "payments.report",
            "credit.view", "masters.view",
        },
    ),
    "GODOWN_SUPERVISOR": (
        "Godown Supervisor",
        {
            "scope.godown_restricted", "inventory.view", "inventory.receive", "inventory.grn_confirm",
            "inventory.adjust", "inventory.transfer_request", "inventory.transfer_dispatch",
            "inventory.transfer_receive", "inventory.count", "dispatch.view", "dispatch.load",
            "dispatch.confirm", "masters.view",
        },
    ),
    "DRIVER": (
        "Driver / Delivery Staff",
        {"scope.own_trips_only", "delivery.update_status", "delivery.pod"},
    ),
}
