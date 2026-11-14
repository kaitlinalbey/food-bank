# Central API Reference

This document lists every endpoint that the central Flask API (`server.py`) provides. We generated the list from the app's URL map and captured the example responses from a test run on a temporary database with fictional data. IDs, tokens and timestamps will be different on your machine.

- **Base URL (local demo):** `http://127.0.0.1:5000`
- **Interactive page:** `GET /docs` (HTML) and `GET /api/openapi.json` (machine-readable list of endpoints)
- **Format:** request and response bodies are JSON, except where noted (CSV import/export, donation receipt)

## 1. Authentication

1. Log in with `POST /api/login`. The response contains a `token`.
2. Send the token on every other request: `Authorization: Bearer <token>`.
3. Tokens expire after **12 hours** or at `POST /api/logout`. The database only stores a SHA-256 hash of each token.

Only `/api/health`, `/api/login`, `/api/openapi.json` and `/docs` work without a token.

A user belongs to exactly one bank. Most endpoints act on **the logged-in user's bank**, which comes from the token, never from the request body. Login is limited per bank and username: after 8 failed attempts in 15 minutes, further attempts get `429`.

### `POST /api/login`

```json
{ "bank_id": 1, "username": "staff", "password": "demo123" }
```

`200`:

```json
{ "bank_id": 1, "bank_name": "Central Food Pantry", "role": "staff",
  "token": "RyNLSbDm...", "username": "staff" }
```

Wrong password: `401 {"error": "Invalid username or password."}`

A correct **demo** password (`demo123`, `admin123`) when classroom demo mode is off (`FOOD_BANK_DEMO_MODE` not set): `403 {"error": "This account still uses a public demo password, which is disabled outside classroom demo mode. ..."}`. No token is issued, and the attempt is recorded in `audit_events`. In demo mode, which the Windows launchers and Docker Compose turn on, the demo accounts work normally.

### Other auth endpoints

| Method and path | Permission | Response |
| --- | --- | --- |
| `POST /api/logout` | logged in | `{"message": "Logged out."}`. The token stops working. |
| `GET /api/me` | logged in | `{"active": 1, "bank_id": 1, "id": 1, "role": "staff", "username": "staff"}` |

## 2. Roles and permissions

| Role | Read own bank | Edit inventory, donations, waste | Request and process transfers | Drivers and vehicles | Approve counts, storage capacity, discrepancy review | Admin endpoints |
| --- | --- | --- | --- | --- | --- | --- |
| `viewer` | yes | no | no | no | no | no |
| `driver` | yes | no | no | no | no | no |
| `staff` | yes | yes | yes | no | no | no |
| `dispatcher` | yes | no | yes | yes | no | no |
| `manager` | yes | yes | yes | yes | yes | no |
| `admin` | yes | yes | yes | yes | yes | yes |

The admin role is system-wide. An admin's own inventory actions still apply to the bank its account belongs to.

A request from the wrong role gets `403 {"error": "Action not permitted for role viewer."}`. An admin-only endpoint called by any other role gets `403 {"error": "Administrator account required."}`.

## 3. Errors and status codes

Every error the API returns is JSON: `{"error": "<message>"}`. Unexpected server errors return `500 {"error": "Internal server error. Report request ID <id>."}`. The details are written to the server log only.

| Code | Meaning | Example |
| --- | --- | --- |
| `200` | Success, including a repeated request that was already done | `{"created": false, "exchange_id": 5, ...}` |
| `201` | Something was created | new transfer, donation, item, user |
| `400` | Bad input | `{"error": "Request body must be a JSON object."}` |
| `401` | Missing, expired or invalid token | `{"error": "Login required."}` |
| `403` | Wrong role, another bank's resource, or a demo password outside demo mode | `{"error": "Only the appropriate food bank can perform this action."}` |
| `404` | Unknown record or route | `{"error": "Route not found."}` |
| `405` | Wrong HTTP method | `{"error": "HTTP method not allowed."}` |
| `409` | Conflict with current data: not enough stock, wrong transfer state, stale version, duplicate name, or a reused key with different details | `{"error": "Cannot receive a transfer with status received."}` |
| `413` | Request body over 2 MB | `{"error": "Request body is too large (2 MB limit)."}` |
| `429` | Too many failed logins | `{"error": "Too many failed attempts. Retry in 15 minutes."}` |

Validation, permission and business rule failures (`400`-`409`) are also written to the `audit_events` table. Bank nodes report `503` to the user when the API cannot be reached. The API itself does not return `503`.

Every response includes an `X-Request-ID` header. If the request sent a valid `X-Request-ID` (8-80 letters, digits, `-` or `_`), the response repeats it. Otherwise the API generates one.

## 4. Health, dashboard and history

| Method and path | Permission | Notes |
| --- | --- | --- |
| `GET /api/health` | public | `{"service": "food-bank-central-api", "status": "ok"}`. Only shows that the process is answering. |
| `GET /api/dashboard` | logged in | Everything the node dashboard needs: `bank`, `inventory`, `matches`, `incoming`, `outgoing`, `notifications`, `unread_count`, `pending_count`, `activity`, `stats`, `alerts`, `forecast`, `recent_donations`, `recent_waste`, `user` |
| `GET /api/stats` | logged in | `{"bank_count": 3, "completed_transfers": 0, "inventory_items": 8, "pending_requests": 0, "shortage_items": 4, "surplus_items": 3}` |
| `GET /api/activity` | logged in | Last 100 activity rows for your bank |
| `GET /api/notifications` | logged in | Last 100 notifications for your bank |
| `POST /api/notifications/read` | staff, manager, admin, dispatcher | `{"count": 0, "message": "Marked 0 notifications as read."}` |
| `GET /api/nodes` | logged in | Checks `/node-health` on each bank's node (port 5000 + bank ID, first 30 banks). `{"nodes": [{"bank_id": 1, "bank_name": "Central Food Pantry", "online": true, "port": 5001}, ...]}` |
| `GET /api/banks` | logged in | Bank `id`, `name`, `area`, `latitude`, `longitude` |
| `GET /api/analytics` | logged in | Bank metrics (system-wide for admin) |
| `GET /api/events` | logged in | Recent request traces for your bank (all banks for admin). No request bodies. |
| `GET /api/traces/<trace_id>` | logged in | All logged requests with that correlation ID. Non-admins only see their own bank's. |

Example `GET /api/traces/demo-trace-0001`:

```json
{ "correlation_id": "demo-trace-0001", "count": 1,
  "events": [{ "bank_id": 1, "method": "GET", "path": "/api/me", "status_code": 200,
               "latency_ms": 1.45, "origin": "central_api", "created_at": "2026-11-10 01:38:12" }] }
```

## 5. Inventory

### Inventory row fields

Rows in `GET /api/dashboard` (`inventory`) and `GET /api/banks/<own id>/inventory` look like this:

```json
{ "item_id": 13, "item_name": "All-Purpose Flour", "category": "Baking", "unit": "bags",
  "quantity": 9, "target": 20, "reserved": 0, "usable": 9, "surplus": 0, "shortage": 11,
  "expired": false, "expiration_date": "2027-07-07", "lot_count": 1, "version": 1,
  "updated_at": "2026-11-10T01:31:22Z" }
```

| Field | Meaning |
| --- | --- |
| `quantity` | Physical units on hand (sum of all lots) |
| `usable` | Units that are not expired and not recalled |
| `reserved` | Units promised to approved transfers that have not shipped yet |
| `surplus` | `usable - target - reserved`, never below 0. This is what other banks can request. |
| `shortage` | `target - usable - incoming approved/in-transit transfers`, never below 0. Expired and recalled units do not count toward the target. |
| `version` | Goes up whenever the quantity changes. Used for offline conflict checks. |

### Endpoints

| Method and path | Permission | Body | Success |
| --- | --- | --- | --- |
| `GET /api/items` | logged in | | Shared catalog: `id`, `name`, `unit`, `category`, `barcode` |
| `POST /api/inventory` | staff, manager, admin | `{"name": "Oat Cereal", "unit": "boxes", "category": "Breakfast", "quantity": 6, "target": 10, "expiration_date": "2027-02-01"}` (date optional) | `201 {"item_id": 9, "message": "Item added."}` |
| `PUT /api/inventory/<item_id>` | staff, manager, admin | `{"quantity": 12, "target": 20, "expiration_date": null}` | `{"message": "Inventory updated."}` |
| `DELETE /api/inventory/<item_id>` | staff, manager, admin | | `{"message": "Item removed."}` |
| `GET /api/banks/<bank_id>/inventory` | logged in | query: `q`, `category`, `status` | see below |
| `GET /api/matches` | logged in | query: `max_miles` (0-500, optional) | see section 6 |

Rules:
- If the item name already exists in the catalog with a different unit or category, `POST /api/inventory` gets `409`. Adding an item your bank already has also gets `409`.
- `PUT` cannot set the quantity below the reserved amount (`409`).
- `DELETE` only works when the quantity is 0 and the item has no open transfers (`409` otherwise).

### `GET /api/banks/<bank_id>/inventory`

For **your own bank**, you get full rows and `status` can be `all`, `in_stock`, `out_of_stock`, `shortage`, `surplus`, `expired` or `at_target`.

For **another bank**, private fields are removed before the response is built, and `status` can be `all`, `available` or `unavailable`:

```json
GET /api/banks/2/inventory?q=rice
{ "bank": {"id": 2, "name": "Northside Food Pantry", "area": "North Little Rock area"},
  "inventory": [
    {"item_id": 11, "item_name": "Brown Rice", "category": "Dry Goods", "unit": "bags", "available": 3},
    {"item_id": 1,  "item_name": "Rice",       "category": "Dry Goods", "unit": "bags", "available": 28}],
  "total": 48, "filtered_total": 2, "categories": ["Bakery", "Baking", "..."],
  "filters": {"q": "rice", "category": "", "status": "all"}, "shared": true }
```

More examples: `SEARCHABLE_INVENTORIES.md`.

## 6. Matching

`GET /api/matches` lists, for each item your bank is short on, every other bank whose `surplus` for that item is above 0. Each result looks like this:

```json
{ "item_id": 13, "item_name": "All-Purpose Flour", "category": "Baking", "unit": "bags",
  "source_bank_id": 2, "source_bank_name": "Northside Food Pantry", "area": "North Little Rock area",
  "needed": 11, "available": 18, "suggested_quantity": 11, "distance_miles": 2.0,
  "expiration_date": "2027-07-07", "expiry_days": 240, "recommendation_score": 106.0 }
```

The results are sorted by item name, then by score (highest first), then by distance. Section 9 of `FINAL_TECHNICAL_REPORT.md` explains how the score is calculated.

## 7. Transfers (exchanges)

| Method and path | Who | From state, to state | Effect on stock |
| --- | --- | --- | --- |
| `POST /api/exchanges` | requesting bank (staff, manager, admin, dispatcher) | none, to `pending` | none |
| `POST /api/exchanges/<id>/approve` | supplying bank | `pending` to `approved` | reserves units at the supplier |
| `POST /api/exchanges/<id>/decline` | supplying bank | `pending` to `declined` | none |
| `POST /api/exchanges/<id>/ship` | supplying bank | `approved` to `in_transit` | removes usable lots from the supplier, oldest expiration first |
| `POST /api/exchanges/<id>/receive` | requesting bank | `in_transit` to `received` | adds the shipped lots to the requester |
| `POST /api/exchanges/<id>/receive-detailed` | requesting bank (staff, manager, admin) | `in_transit` to `received` | adds only the accepted units, and records a discrepancy |
| `POST /api/exchanges/<id>/cancel` | requesting bank | `pending` or `approved` to `canceled` | releases the reservation |
| `POST /api/exchanges/<id>/accept` | supplying bank | same as `approve` | kept for older clients |
| `PUT /api/exchanges/<id>/schedule` | supplying bank (staff, manager, admin) | `approved` only | `{"pickup_at": "2026-12-02T13:00:00Z", "delivery_at": "2026-12-02T16:00:00Z", "carrier_notes": "Volunteer pickup"}`. Times must include a timezone. |
| `PUT /api/exchanges/<id>/transport` | staff, manager, admin, dispatcher | | `{"driver_id": 1, "vehicle_id": 1}` |

### Creating a transfer

```json
POST /api/exchanges
{ "source_bank_id": 2, "item_id": 13, "quantity": 2, "request_key": "demo-request-0001" }
```

- `201 {"created": true, "exchange_id": 5, "message": "Request sent."}`
- Same body sent again: `200 {"created": false, "exchange_id": 5, "message": "Request already exists."}`
- Same key with different details: `409 {"error": "Request key is already associated with a different request."}`
- `request_key` is required: 8-100 letters, digits, `-` or `_`. Use a new key for each new request, and the same key when retrying.
- Quantity above your shortage or the supplier's surplus: `409`.

### Changing state

```text
POST /api/exchanges/5/approve   (as Northside)   -> 200 {"message": "Transfer approved.", "status": "approved"}
POST /api/exchanges/5/approve   (as Central)     -> 403 {"error": "Only the appropriate food bank can perform this action."}
POST /api/exchanges/5/ship      (as Northside)   -> 200 {"message": "Transfer in transit.", "status": "in_transit"}
POST /api/exchanges/5/receive   (as Central)     -> 200 {"message": "Transfer received.", "status": "received"}
POST /api/exchanges/5/receive   (again)          -> 409 {"error": "Cannot receive a transfer with status received."}
```

Approve re-checks the surplus and shortage inside the transaction, so two approvals that compete for the same stock cannot both succeed.

`receive-detailed` body: `{"received_qty": 8, "damaged_qty": 1, "notes": "One case missing"}`. Damaged and missing units are not added to inventory.

## 8. Lots, donations, waste, CSV and barcodes

| Method and path | Permission | Body / response |
| --- | --- | --- |
| `POST /api/donations` | staff, manager, admin | `{"item_id": 7, "quantity": 4, "donor": "Fictional Youth League", "expiration_date": "2027-03-01"}` gives `201 {"donation_id": 1, "lot_id": 25, "message": "Donation recorded."}`. Creates a new lot. |
| `GET /api/donations` | logged in | Recent donations for your bank |
| `GET /api/donations/<id>/receipt` | logged in, own bank | **Plain text** acknowledgment (donation number, bank, donor, item, quantity, batch code, date) |
| `POST /api/waste` | staff, manager, admin | `{"item_id": 7, "quantity": 1, "reason": "Damaged package", "lot_id": 3}` (`lot_id` optional) gives `201 {"message": "Waste recorded.", "quantity": 1}`. Reserved stock cannot be discarded (`409`). |
| `GET /api/waste` | logged in | Recent waste records |
| `GET /api/lots` | logged in | Your bank's lots that still have stock |
| `PATCH /api/lots/<lot_id>` | staff, manager, admin | `{"expiration_date": "2027-01-15"}` gives `{"message": "Batch expiration updated."}` |
| `GET /api/forecast` | logged in | 30-day average outflow per item, plus `alerts` (low stock, expiring soon). This is a simple estimate. |
| `GET /api/inventory/export.csv` | logged in | **`text/csv`** with columns `name,unit,category,barcode,quantity,target,expiration_date` |
| `POST /api/inventory/import.csv` | staff, manager, admin | **`text/csv`** body with the same columns. Up to 100 rows. All rows are applied or none. |
| `GET /api/barcodes/<code>` | logged in | `{"item": {"id": 7, "name": "Apples", "barcode": "DEMO-0001", ...}}` |
| `PUT /api/admin/items/<item_id>/barcode` | admin | `{"barcode": "DEMO-0001"}`. Barcodes must be unique (`409`). |

## 9. Offline synchronization

These two endpoints are what bank nodes call when they replay their offline queue. Both are safe to retry with the same `op_key`.

### `POST /api/inventory/<item_id>/adjust` (staff, manager, admin)

```json
{ "delta": 3, "base_version": 0, "op_key": "offline-op-0001" }
```

| Situation | Response |
| --- | --- |
| Version matches | `200 {"duplicate": false, "message": "Offline change synchronized.", "operation_id": 1}` |
| Same `op_key` and details again | `200 {"duplicate": true, "message": "Already synchronized.", "operation_id": 1}` |
| Item changed since `base_version` | `409 {"error": "Conflict: item changed from version 0 to 1. Review and re-enter the adjustment."}`. Nothing is changed. |
| Same `op_key`, different details | `409 {"error": "An operation with this key already has different details."}` |

### `POST /api/offline/receipts` (staff, manager, admin)

```json
{ "item_id": 7, "quantity": 3, "donor": "Fictional Donor Group",
  "op_key": "receipt-0001-abcdef", "lot_code": "OFFLINE-TEST01" }
```

- First time: `201 {"duplicate": false, "message": "Offline receipt safely merged.", "receipt_id": 1}`
- Again: `201 {"duplicate": true, "message": "Offline receipt already synchronized.", "receipt_id": 1}`
- Same key with different data: `409`

A receipt always creates a new lot, so it does not need a version check.

## 10. Recalls, storage, counts, transport and discrepancies

| Method and path | Permission | Body |
| --- | --- | --- |
| `GET /api/donors` / `POST /api/donors` | logged in / staff, manager, admin | `{"name": "Fictional Community Drive", "notes": "Demo label"}` |
| `GET /api/recalls` | logged in | Recalls that affect your bank (all recalls for admin) |
| `POST /api/admin/recalls` | admin | `{"item_id": 1, "lot_code": "LOT-ABC123", "reason": "Supplier notice"}`. Marks every lot with that code as recalled, at every bank. `404` if no stored or in-transit batch matches. |
| `POST /api/admin/recalls/<id>/close` | admin | `{}`. Releases the quarantine. |
| `GET /api/storage` | logged in | Capacity and occupied units for `ambient`, `refrigerated` and `frozen` |
| `PUT /api/storage/<storage_type>` | manager, admin | `{"capacity_units": 5000}` |
| `PUT /api/admin/items/<item_id>/storage` | admin | `{"storage_type": "refrigerated"}` |
| `GET /api/reconciliations` / `POST /api/reconciliations` | logged in / staff, manager, admin | `{"item_id": 7, "counted_qty": 11, "reason": "Shelf count"}` gives `201 {"message": "Physical count saved for manager review.", "reconciliation_id": 1}` |
| `POST /api/reconciliations/<id>/apply` or `/reject` | manager, admin | `{}`. `apply` fails with `409` if the item changed after the count. |
| `GET /api/transport` | logged in | Your bank's drivers, vehicles and assignments |
| `POST /api/drivers` | manager, admin, dispatcher | `{"display_name": "Driver A"}` |
| `POST /api/vehicles` | manager, admin, dispatcher | `{"label": "Van A", "capacity_units": 100, "refrigerated": true}` |
| `GET /api/discrepancies` | logged in | Shipment discrepancies for your bank |
| `POST /api/discrepancies/<id>/review` | manager, admin | `{}` |

## 11. Administration (admin only)

| Method and path | Body or query | Behavior |
| --- | --- | --- |
| `POST /api/admin/banks` | `{"name": "Southside Food Pantry", "area": "South", "latitude": 34.7, "longitude": -92.25, "staff_password": "demo-pass-123"}` | `201 {"bank_id": 4, "message": "Bank created.", "suggested_node_port": 5004}`. Also creates a `staff` account. Start its node with `python node.py --bank 4 --port 5004`. |
| `GET /api/admin/banks` | `q`; `probe=0` skips the node check | Each bank with inventory rows, active users, open transfers, last contact time and whether its node is online |
| `PUT /api/admin/banks/<id>` | any of `name`, `area`, or `latitude` together with `longitude` | Duplicate name gives `409`. Bad coordinates give `400`. |
| `DELETE /api/admin/banks/<id>` | `{"confirm_name": "<exact bank name>"}` | Only works for a bank that has never been used. Otherwise `409` with the list of blocking records. |
| `POST /api/admin/users` | `{"bank_id": 1, "username": "pat", "password": "demo-pass-123", "role": "viewer"}` | `201 {"message": "User created.", "user_id": 6}`. Role is one of `staff`, `manager`, `dispatcher`, `driver`, `viewer` or `admin`. |
| `GET /api/admin/users` | `q`, `bank_id`, `role`, `active` (`1`/`0`) | `id`, `bank_id`, `bank_name`, `username`, `role`, `active`. Never password hashes. |
| `PUT /api/admin/users/<id>` | `role` and/or `bank_id`; plus `confirm_username` when granting `admin` | Ends that user's sessions. You cannot change your own account or demote the last admin (`409`). |
| `POST /api/admin/users/<id>/disable` | `{"confirm_username": "...", "reason": "..."}` | Ends the user's sessions. You cannot disable yourself or the last admin. |
| `POST /api/admin/users/<id>/enable` | `{"reason": "..."}` (optional) | |
| `GET /api/admin/overview` | | Live counts (banks, items, lots, units, users, transfers by state), alerts, recent sync errors, `integrity_quick_check`, latest verified backup |
| `GET /api/admin/health` | | Read-only checks: SQLite integrity and foreign keys, inventory totals equal lot totals, no negative quantities, approved transfers still covered, shipped lots match quantities, recalled lots quarantined. Returns `overall` = `pass`, `warn` or `fail`. |
| `GET /api/admin/backups` | | The last 50 backups, with checksum, verification result and last restore-test result |
| `POST /api/admin/backups` | `{"note": "..."}` (optional) | `201`. Online SQLite backup to `admin_backups/` next to the database, then verified. |
| `POST /api/admin/backups/<id>/verify-restore` | `{}` | Restores into a temporary copy, runs every check, deletes the copy. Returns `passed`, `steps[]` and `active_database_overwritten: false`. |
| `GET /api/admin/system/status` | | Database size and integrity, node status, transfer counts by state, recalls, recorded HTTP events and errors |
| `GET /api/admin/audit` | | Recent failed API operations and activity |
| `GET /api/admin/lab/scenarios` | | The 8 test-lab scenarios: `backup_restore`, `concurrent`, `delivery`, `multi_bank`, `offline_receipt`, `outage`, `recall`, `reconciliation` |
| `POST /api/admin/lab/run` | `{"scenario": "all"}` or one scenario name | Runs the scenario on temporary databases. Live data is never touched. |

None of these endpoints return passwords, password hashes or tokens. Successful admin changes are recorded in `activity` and `audit_events`. There is deliberately no API for replacing the live database. See `DATABASE_MAINTENANCE.md`.

## 12. Documentation endpoints

| Method and path | Permission | Notes |
| --- | --- | --- |
| `GET /docs` | public | HTML page that lists the endpoints and lets you try them with a token |
| `GET /api/openapi.json` | public | Simple OpenAPI 3.0 description, listing paths and methods only |
