# Multi-page navigation

The bank node website (`node.py`) is split into separate pages. Each page has one job, and the left sidebar links to all of them. This document lists every page, what it replaced, how old links are handled, and where each form sends the user after it is submitted.

Only the website layer changed. The central API, the database, the POST routes and their field names, CSRF protection, idempotency keys, role checks and offline behaviour are the same as before.

## 1. Layout

Every page uses `templates/base.html`:

- **Sidebar** (dark navy, teal accents) with the bank name, the signed-in user and role, the page links and a **Log out** button. The current page is highlighted and marked `aria-current="page"`.
- **Phones and narrow windows:** the sidebar is hidden behind a **Menu** button at the top. It uses a CSS checkbox, so it works without JavaScript.
- **Messages** after an action appear at the top of the page (`<div class="messages">`). Errors are red, confirmations green.
- **Content** uses cards for numbers, panels for sections and tables that scroll sideways on small screens instead of widening the page.

Styles are in `static/style.css`. Colours are CSS variables at the top of that file.

## 2. Pages

| Sidebar label | Route | Endpoint | Template | Who can open it | Purpose |
|---|---|---|---|---|---|
| Dashboard | `GET /` | `home` | `dashboard.html` | any signed-in user | Overview cards (inventory items, shortages, surplus items, pending requests, received transfers), Needs attention (transfers waiting, offline changes, nodes online), important alerts, quick links, notifications, recent activity |
| My inventory | `GET /my-inventory` | `my_inventory_page` | `inventory.html` | any signed-in user | This bank's stock. Search, category and status filters, sortable columns. Read-only by default. **Edit inventory** (`?inventory=edit`) is shown to staff, managers and admins. |
| Other food banks | `GET /other-banks` | `other_banks_page` | `other_banks.html` | any signed-in user | One other bank at a time, public fields only (item, category, unit, available to share). Read-only. |
| Supply matching | `GET /matching` | `matching_page` | `matching.html` | any signed-in user | Our shortages and the banks that can cover them. **Request** buttons for staff, managers, admins and dispatchers. |
| Transfers | `GET /transfers` | `transfers_page` | `transfers.html` | any signed-in user | All transfers in one table with tabs: All, Requests to us, Our requests, Pending, Approved, In transit, Completed (`?tab=`). Approve, decline, ship, receive and cancel buttons follow the same role rules as before. |
| Donations and waste | `GET /donations` | `donations_page` | `donations.html` | any signed-in user | Record a donation, report waste, donation and waste records, donors |
| Batches and recalls | `GET /batches` | `batches_page` | `batches.html` | any signed-in user | Expiration alerts, batches with filter tabs (All, Expiring, Expired, Recalled; `?lots=`), batch expiry form, recalls, storage capacity |
| Transportation | `GET /transport` | `transport_page` | `transport.html` | any signed-in user | Pickup and delivery schedules, drivers and vehicles, transport assignment, delivery problems |
| Offline sync | `GET /offline` | `offline_page` | `offline_sync.html` | any signed-in user | Connection status, saved offline changes, offline donation receipts, sync. Admins also see the classroom outage switch (demo mode only). |
| System status | `GET /project-status` | `project_status` | `project_status.html` | any signed-in user | Node status, API health, request trace lookup, central and node events |
| More tools | `GET /more` | `more_page` | `more.html` | any signed-in user | Spreadsheet import/export, barcodes, physical counts, usage forecast, analytics, API documentation, and the Test lab for admins |
| Administration | `GET /admin` | `admin_page` | `admin.html` | admins only (others get 403) | Banks, users, health, backups, demo reset, audit records |

`GET /advanced` (endpoint `advanced_page`, template `moved.html`) still exists, but only to forward old links (section 4).

`/offline` is the only page that works while the central API is unreachable. It then returns HTTP 503 with the saved snapshot and the queue forms. Every other page shows the offline screen (`offline.html`, HTTP 503) during an outage, as before.

## 3. Where the old sections went

| Old location | New location |
|---|---|
| Dashboard: My inventory, Edit Inventory | My inventory (`/my-inventory`, `/my-inventory?inventory=edit`) |
| Dashboard: Other food bank inventories | Other food banks (`/other-banks`) |
| Dashboard: Available supply matches | Supply matching (`/matching`) |
| Dashboard: Requests to us, Our requests | Transfers (`/transfers?tab=incoming`, `/transfers?tab=outgoing`) |
| Dashboard: Donations and food waste | Donations and waste (`/donations`) |
| Dashboard: Expiration | Batches and recalls (`/batches`) |
| Dashboard: Pickup and delivery scheduling | Transportation (`/transport#schedules`) |
| Dashboard: Offline mode and sync | Offline sync (`/offline`) |
| Dashboard: Spreadsheet import/export, Barcodes, Forecast, Developer links | More tools (`/more#csv`, `#barcodes`, `#forecast`, `#api`) |
| Dashboard: Node status | System status (`/project-status`) and the Dashboard card |
| Dashboard: Add bank, Add user | Administration (`/admin#banks`, `/admin#users`) |
| Dashboard: Notifications | Dashboard (`/#notifications`) |
| Additional features: Donors | Donations and waste (`/donations#donors`) |
| Additional features: Recalls, Storage | Batches and recalls (`/batches#recalls`, `#storage`) |
| Additional features: Physical counts | More tools (`/more#counts`) |
| Additional features: Drivers, vehicles, transport | Transportation (`/transport#fleet`) |
| Additional features: Delivery problems | Transportation (`/transport#discrepancies`) |
| Additional features: Offline donation receipts | Offline sync (`/offline#receipts`) |
| Additional features: Analytics | More tools (`/more#analytics`) |
| Additional features: Test lab (admin) | More tools (`/more#lab`) |
| Additional features: Events and traces | System status (`/project-status#events`) |
| (new) Audit records | Administration (`/admin#audit`) |

## 4. Old links and bookmarks

Old links keep working. None of the redirects use a URL taken from the request; the server and the scripts only pick from fixed lists of internal pages.

- `/?q=…`, `/?category=…`, `/?status=…` and `/?inventory=edit` redirect to `/my-inventory` with the same filters. The filters are validated by `parse_filters` first, and unknown values are dropped.
- `/?other_bank=…`, `/?other_q=…`, `/?other_category=…`, `/?other_status=…` redirect to `/other-banks`. `other_bank` must be a whole number.
- Old dashboard anchors such as `/#incoming`, `/#offline` or `/#csv` are forwarded by a short script in `dashboard.html` that looks the anchor up in a fixed table. Without JavaScript the user stays on the Dashboard, which links to every page.
- `/advanced` and its anchors (`/advanced#recalls`, `#lab`, …) open `moved.html`, which forwards known anchors the same way and otherwise shows a list of links to the new pages.

## 5. Where forms return

After a form is submitted, the node redirects to the page the form belongs to. The targets come from two fixed tables in `node.py`, `RETURN_PAGES` and `ADVANCED_ACTION_PAGES`, and from `back()`.

| Form (POST route) | Returns to |
|---|---|
| Save inventory, add item, delete item (`/inventory`, `/inventory/add`, `/inventory/<id>/delete`) | `/my-inventory?inventory=edit` with the current filters and sort |
| Request transfer (`/request`) | `/transfers?tab=outgoing` |
| Approve, decline, ship (`/exchange/<id>/<action>`) | `/transfers?tab=incoming` |
| Receive, cancel (`/exchange/<id>/<action>`) | `/transfers?tab=outgoing` |
| Schedule pickup or delivery (`/exchange/<id>/schedule`) | `/transport#schedules` |
| Mark notifications read (`/notifications/read`) | `/#notifications` |
| Donation, waste (`/donation`, `/waste`) | `/donations#records`, `/donations#waste` |
| Donor (`/advanced/action/donor`) | `/donations#donors` |
| Batch expiry (`/lot/<id>/expiry`) | `/batches#batches` |
| Recall, close recall, storage (`/advanced/action/recall`, `/advanced/recall/<id>/close`, `/advanced/action/storage`, `item_storage`) | `/batches#recalls`, `/batches#storage` |
| Driver, vehicle, transport assignment | `/transport#fleet` |
| Delivery receipt, review problem | `/transport#discrepancies` |
| Offline queue, sync, discard (`/offline/queue`, `/offline/sync`, `/offline/discard/<id>`) | `/offline#queue` |
| Offline receipts (`/advanced/receipt/…`) | `/offline#receipts` |
| Outage switch (`/offline/simulate`) | `/offline` |
| Spreadsheet import/export, barcodes | `/more#csv`, `/more#barcodes` |
| Physical count, apply/reject count | `/more#counts` |
| Test lab (`/advanced/lab`) | `/more#lab` |
| Add bank, add user and all `/admin/…` forms | `/admin` and the matching section |

The `/advanced/...` POST routes kept their old paths so that saved forms, scripts and tests still work.

## 6. Checking navigation after a change

- `tests/test_multipage_ui.py` loads every page for each role, checks the active link, that hidden controls are still refused by the server, sorting and filters, the return targets above, the old-link redirects and the offline pages.
- `verify_ui.py` walks through every page over real HTTP with the API and three nodes running (67 steps).
- `verify_distributed.py` runs the 18 multi-node scenarios.
