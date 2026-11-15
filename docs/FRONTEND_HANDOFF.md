# Frontend Handoff

This guide is for whoever redesigns the user interface. The current pages are deliberately plain, so you can restyle them without touching the backend, as long as you follow the rules in sections 5 and 6.

The frontend is **server-rendered Flask and Jinja2**. There is no JavaScript framework, npm or build step. `{{ ... }}` and `{% ... %}` in the templates run on the server.

## 1. Templates and the pages they control

All page templates are in `templates/`. Every template except `api_docs.html` extends `base.html`. The site is split into separate pages; `MULTIPAGE_NAVIGATION.md` lists where each old dashboard section went and how old links are forwarded.

| Template | Page / URL (on each bank node, e.g. http://127.0.0.1:5001) | Rendered by (`node.py`) | Who sees it |
| --- | --- | --- | --- |
| `base.html` | Shared layout: sidebar with bank, user, page links and Log out; Menu button on narrow screens; flash messages | every page | everyone |
| `login.html` | `GET /login` | `login_page` | not logged in |
| `dashboard.html` | `GET /`. Overview cards, Needs attention, alerts, quick links, notifications, recent activity | `home` | logged in |
| `inventory.html` | `GET /my-inventory`. This bank's stock: search, filters, sortable columns, View and Edit tabs | `my_inventory_page` | logged in (Edit tab: staff, manager, admin) |
| `other_banks.html` | `GET /other-banks`. One other bank's public inventory, read-only | `other_banks_page` | logged in |
| `matching.html` | `GET /matching`. Our shortages and matching supply at other banks | `matching_page` | logged in |
| `transfers.html` | `GET /transfers?tab=...`. All transfers with status tabs and actions | `transfers_page` | logged in |
| `donations.html` | `GET /donations`. Donations, waste, donors | `donations_page` | logged in |
| `batches.html` | `GET /batches?lots=...`. Expiration alerts, batches, recalls, storage | `batches_page` | logged in |
| `transport.html` | `GET /transport`. Schedules, drivers and vehicles, delivery problems | `transport_page` | logged in |
| `offline_sync.html` | `GET /offline`. Connection, offline queue, offline receipts, outage switch. Works without the central API (then HTTP 503). | `offline_page` | logged in |
| `more.html` | `GET /more`. CSV, barcodes, physical counts, forecast, analytics, API documentation, Test lab (admin) | `more_page` | logged in |
| `project_status.html` | `GET /project-status` and `GET /project-trace?id=...`. Node status, API health, trace lookup, events | `project_status`, `project_trace` | logged in (system-wide summary is admin only) |
| `admin.html` | `GET /admin`. Database administration | `admin_page` | admin only (others get 403) |
| `moved.html` | `GET /advanced`. Forwards old "Additional features" links to the new pages | `advanced_page` | logged in |
| `offline.html` | Shown instead of a page (HTTP 503) when the central API cannot be reached | every page except `/offline` | logged in |
| `api_docs.html` | `GET /docs` on the **central API** (port 5000), not on the nodes | `routes/docs.py` | public |
| `error.html` | Not used by any route at the moment | — | — |

### Navigation and page sections

The sidebar in `base.html` links to every page. Links are built with the `navlink(endpoint, label)` macro, which marks the current page with `class="active"` and `aria-current="page"` (the context variable `active_page` holds `request.endpoint`). The Administration link is only shown when `is_admin` is true.

Section ids per page:

| Template | Section ids |
| --- | --- |
| `dashboard.html` | `overview`, `attention`, `alerts`, `quick-links`, `notifications`, `activity` |
| `inventory.html` | `inventory` |
| `other_banks.html` | `other-inventories` |
| `matching.html` | `matches`, `shortages` |
| `transfers.html` | `transfers` |
| `donations.html` | `receive`, `report-waste`, `records`, `waste`, `donors` |
| `batches.html` | `alerts`, `batches`, `recalls`, `storage` |
| `transport.html` | `schedules`, `fleet`, `discrepancies` |
| `offline_sync.html` | `connection`, `queue`, `receipts` |
| `more.html` | `csv`, `barcodes`, `counts`, `forecast`, `analytics`, `api`, `lab` (admin) |
| `project_status.html` | `nodes`, `system`, `trace`, `events`, `node-events` |
| `admin.html` | `overview`, `banks`, `users`, `health`, `backups`, `demo`, `audit` |

**Keep these ids.** The redirects after each form, the old-link forwarding and the automated checks use them. Two tags must stay exactly as written, because tests split the page on them: `<section id="inventory">` and `<section id="other-inventories">` (no extra attributes).

**After a form is submitted**, the node redirects to the page and section the form belongs to. For example, requesting a transfer returns to `/transfers?tab=outgoing`, approving one to `/transfers?tab=incoming`, and syncing the offline queue to `/offline#queue`. The target comes from the fixed tables `RETURN_PAGES` and `ADVANCED_ACTION_PAGES` in `node.py`, never from user input. Inventory edits return to `/my-inventory?inventory=edit` with the current filters and sort. If you move a section to another page, update those tables, the sidebar in `base.html` and the old-link maps in `dashboard.html` and `moved.html`. The full table is in `MULTIPAGE_NAVIGATION.md`, section 5.

## 2. CSS

There is **one stylesheet**: `static/style.css`. `base.html` loads it with `url_for('static', filename='style.css')`. No external CSS, fonts or icons are loaded.

Colours, spacing and the sidebar width are CSS variables in `:root` at the top of the file. The main classes:

| Class | Used for |
| --- | --- |
| `layout`, `sidebar`, `side-nav`, `nav-group`, `active` | Page frame and the left sidebar; `active` marks the current page |
| `topbar`, `nav-toggle` | Menu button and hidden checkbox that open the sidebar on narrow screens |
| `content`, `page-head`, `lead` | Main column, page heading block and its intro line |
| `cards`, `card`, `stat`, `stat-value`, `stat-label`, `stat-warn`, `stat-attn`, `stat-bad` | Number cards on the dashboard |
| `panel`, `grid-2` | Section boxes; two-column grid that collapses on phones |
| `messages`, `flash`, `flash-ok`, `flash-error` | Flash messages in `base.html` |
| `alert`, `note`, `empty` | Warnings, helper notes and empty-table messages |
| `btn-primary`, `btn-small`, `btn-ghost`, `btn-danger` | Buttons |
| `badge`, `tag`, `tag-ok`, `tag-warn`, `tag-bad`, `tag-info`, `tag-attn` | Counts and status labels |
| `formline`, `form-stack`, `short-form` | Forms in a row, stacked forms, one-button forms in table rows |
| `inventory-tabs` | View/Edit and status tab links. The current tab has `aria-current="page"`. |
| `inventory-filters` | Search and filter forms |
| `scroll` | Wrapper that lets wide tables scroll sideways |
| `page-index`, `quick-links`, `plain-list` | In-page link lists |
| `login-card`, `mono`, `small`, `muted` | Login box, monospace values, smaller grey text |

You can replace the whole file. If you rename classes, update the templates at the same time. If you split the CSS into several files, add the extra `<link>` tags in `base.html`.

## 3. Where page data comes from

Nodes never read the database. Each node route calls the central API and passes the JSON to the template.

| Page | Central API calls (`node.py`) | Main template variables |
| --- | --- | --- |
| Dashboard | `GET /api/dashboard`, `GET /api/nodes` | `summary`, `stats`, `alerts`, `notifications`, `unread_count`, `activity`, `nodes`, `bank`, `user`, `simulated` |
| My inventory | `GET /api/dashboard` | `inventory` (full list), `visible_inventory` (filtered and sorted), `own_filters`, `own_categories`, `sort`, `inventory_edit`, `bank` |
| Other food banks | `GET /api/banks`, `GET /api/banks/<id>/inventory` | `other_banks`, `selected_bank`, `other_inventory`, `other_total`, `other_filters`, `other_categories`, `other_error`, `sort` |
| Supply matching | `GET /api/dashboard` | `matches`, `shortages`, `bank` |
| Transfers | `GET /api/dashboard` | `transfers` (rows for the chosen tab, each with `direction`), `tab`, `tabs`, `counts` |
| Donations and waste | `GET /api/dashboard`, `/api/donations`, `/api/waste`, `/api/donors` | `inventory`, `donations`, `waste`, `donors` |
| Batches and recalls | `GET /api/dashboard`, `/api/lots`, `/api/recalls`, `/api/storage` | `lots` (with `days_left`, `state`), `lot_view`, `lot_views`, `lot_counts`, `alerts`, `recalls`, `storage` |
| Transportation | `GET /api/dashboard`, `/api/transport`, `/api/discrepancies` | `to_schedule`, `scheduled`, `arriving`, `fleet`, `discrepancies` |
| Offline sync | `GET /api/dashboard` if reachable, otherwise the node's local file | `online`, `message`, `items`, `queue`, `receipts`, `snapshot`, `simulated` |
| More tools | `GET /api/dashboard`, `/api/reconciliations`, `/api/analytics`, `/api/admin/lab/scenarios` (admin) | `inventory`, `forecast`, `reconciliations`, `analytics`, `lab_scenarios`, `last_lab` |
| Offline screen | none (uses the node's local file) | `message`, `snapshot`, `queue`, `receipt_queue`, `simulated` |
| System status | `GET /api/health`, `/api/nodes`, `/api/events`, `/api/admin/system/status` (admin), `/api/traces/<id>` | `api_health`, `nodes`, `central_events`, `node_events`, `status`, `error`, `trace`, `trace_error`, `queue_count`, `receipt_count` |
| Database administration | `GET /api/admin/overview`, `/api/admin/banks`, `/api/admin/users`, `/api/admin/backups`, `/api/admin/audit`, and `/api/admin/health` when `health=1` | `data.overview`, `data.banks`, `data.users`, `data.backups`, `data.audit`, `data.health`, `errors`, `bank_q`, `user_filters`, `roles`, `restore`, `this_user`, `denied` |

Every page that calls `/api/dashboard` also saves the offline snapshot. Secondary calls (donors, lots, events and so on) show an empty section if they fail, instead of the offline screen.

Available on every page: `csrf_token`, `bank_name`, `new_request_key()`, `session` (`username`, `role`), `active_page`, `is_admin`, `link_offline` and `outage_controls`. `outage_controls` is true only for an administrator in classroom demo mode on the same computer. Show the outage button only when it is true. The server refuses everyone else anyway.

On the offline page, keep three cases apart:
- a simulated outage, with the "Restore simulated connection" form shown only when `outage_controls` is true;
- a simulated outage seen by other users, with a message only;
- a real outage, with a plain "Retry central API connection" link to the current page. This must not be a form that posts to `/offline/simulate`.

The field meanings for inventory rows (`quantity`, `usable`, `reserved`, `surplus`, `shortage`, `expired`, `version`) are explained in `API.md`, section 5.

### Which controls each role sees

Each page template sets `can_edit` and/or `can_transfer` at the top and uses them to hide controls a role cannot use. Some sections also check `role` directly (for example the Test lab and recall closing).

| Variable | True for | Controls it shows |
| --- | --- | --- |
| `can_edit` | staff, manager, admin | Edit inventory tab, add/remove items, donations, waste, batch expiry, CSV import, physical counts, offline queue and sync, scheduling |
| `can_transfer` | staff, manager, admin, dispatcher | Request, Approve, Decline, Mark shipped, Cancel, Confirm received, Mark all read |
| `is_admin` / `role=='admin'` | admin | Administration page link, barcode assignment, recalls (create/close), Test lab |
| `can_fleet` (Transportation page) | manager, admin, dispatcher | Add driver, add vehicle |
| `outage_controls` | admin, in demo mode, on the same computer | Simulate connection outage / Restore |

Viewers and drivers get read-only pages. These checks only hide buttons; the central API refuses the same actions on its own, so never rely on the template for security.

**Search context:** My inventory and Other food banks are separate pages, so each keeps its own filters in its own URL. Choosing a different bank tab on Other food banks resets that bank's search. Column sort links keep the current filters.

## 4. Node form routes (do not rename the fields)

Every POST form **must** include `<input type="hidden" name="_csrf" value="{{ csrf_token }}">`. Without it, the node rejects the form with HTTP 400.

| Route | Method | Fields (besides `_csrf`) |
| --- | --- | --- |
| `/login` | POST | `username`, `password` |
| `/logout` | POST | none |
| `/inventory` | POST | `item_id`, `quantity`, `target`, `expiration_date` (plus `q`, `category`, `status` to keep the filters) |
| `/inventory/add` | POST | `name`, `unit`, `category`, `quantity`, `target`, `expiration_date` |
| `/inventory/<item_id>/delete` | POST | none |
| `/request` | POST | `source_bank_id`, `item_id`, `quantity`, `request_key` |
| `/exchange/<id>/<action>` | POST | none. `action` is `approve`, `decline`, `ship`, `receive` or `cancel`. |
| `/exchange/<id>/schedule` | POST | `pickup_at`, `delivery_at`, `carrier_notes` |
| `/notifications/read` | POST | none |
| `/donation` | POST | `item_id`, `quantity`, `donor`, `donor_id`, `lot_code`, `expiration_date` |
| `/waste` | POST | `item_id`, `quantity`, `reason`, `lot_id` |
| `/lot/<lot_id>/expiry` | POST | `expiration_date`. The Batches page posts to `/lot/0/expiry` with the batch number in `lot_id_entry`, so the form works with or without its small script. |
| `/barcode` | POST | `item_id`, `barcode` (admin) |
| `/barcode/lookup` | GET | `barcode` (query string) |
| `/csv/export` | GET | none |
| `/csv/import` | POST (multipart) | file field `inventory_csv` |
| `/offline/simulate` | POST | none. Toggles the simulated outage: admin only, demo mode only, same computer only. Others get 401, 403 or 404. |
| `/offline/queue` | POST | `item_id`, `delta` |
| `/offline/sync` | POST | none |
| `/offline/discard/<row_id>` | POST | none |
| `/admin/bank` | POST | `name`, `area`, `latitude`, `longitude`, `staff_password`, optional `return_to=admin` |
| `/admin/user` | POST | `bank_id`, `username`, `password`, `role` (`staff`, `manager`, `dispatcher`, `driver`, `viewer` or `admin`), optional `return_to=admin` |
| `/advanced/action/<action>` | POST | `donor`: `name`, `notes`. `recall`: `item_id`, `lot_code`, `reason`. `storage`: `storage_type` (must be `ambient`, `refrigerated` or `frozen`), `capacity_units`. `item_storage`: `item_id`, `storage_type`. `count`: `item_id`, `counted_qty`, `reason`. `driver`: `display_name`. `vehicle`: `label`, `capacity_units`, `refrigerated`. `transport`: `exchange_id`, `driver_id`, `vehicle_id`. `receipt`: `exchange_id`, `received_qty`, `damaged_qty`, `notes`. |
| `/advanced/recall/<id>/close` | POST | none |
| `/advanced/count/<id>/<action>` | POST | none. `action` is `apply` or `reject`. |
| `/advanced/discrepancy/<id>/review` | POST | none |
| `/advanced/receipt/queue` | POST | `item_id`, `quantity`, `donor`, `expiration_date` |
| `/advanced/receipt/sync` | POST | none |
| `/advanced/receipt/<id>/discard` | POST | none |
| `/advanced/lab` | POST | `scenario` (admin) |
| `/admin/banks/<id>/update` | POST | `name`, `area`, `latitude`, `longitude` |
| `/admin/banks/<id>/delete` | POST | `confirm_name` |
| `/admin/users/<id>/update` | POST | `role`, `bank_id`, `confirm_username` (only needed when granting admin) |
| `/admin/users/<id>/disable` | POST | `confirm_username`, `reason` |
| `/admin/users/<id>/enable` | POST | `reason` |
| `/admin/backups` | POST | `note` |
| `/admin/backups/<id>/verify-restore` | POST | none |

GET query parameters used by the dashboard and admin page:

- My inventory: `inventory=edit`, `q`, `category`, `status`, `sort` (`name`, `category`, `quantity`, `shortage`, `surplus`, `expiration`). See `SEARCHABLE_INVENTORIES.md`.
- Other food banks: `other_bank`, `other_q`, `other_category`, `other_status`, `other_sort` (`name`, `category`, `available`).
- Transfers: `tab` (`all`, `incoming`, `outgoing`, `pending`, `approved`, `in_transit`, `completed`). Batches: `lots` (`all`, `expiring`, `expired`, `recalled`).
- Dashboard (`/`): the old inventory and other-bank parameters above are still accepted and redirect to the new pages.
- Admin page: `bank_q`, `q`, `bank_id`, `role`, `active`, `health=1`.

## 5. How to redesign safely

1. **Work on a copy and test with fresh data.** Run `run_presentation_windows.bat` so you never change the real database while experimenting.
2. **Change the HTML and CSS freely.** Layout, headings, ordering, colors, tables versus cards, moving sections between pages: all fine.
3. **Keep every form's `action`, `method` and field `name`s** from section 4, and keep the hidden `_csrf` field.
4. **Use `{{ new_request_key() }}`** for the `request_key` field. It must be different for each new request form, and the same if you add a "retry" for that one submission.
5. **Keep the role checks in templates** (for example `{% if user.role in ... %}` and `{% if session.get('role')=='admin' %}`), so users are not shown buttons they cannot use. The API still enforces permissions on its own.
6. **Use `visible_inventory` for the inventory table** and leave `inventory` for the other forms (donations, transfers), because those need the full list.
7. **Show errors and conflicts clearly.** Flash messages carry the API's error text. Do not hide offline conflicts or retry them automatically.
8. **Check the layout at phone and desktop widths** (for example 375px and 1280px) and keep wide tables scrollable.
9. **Keep the section ids, empty-state messages and role checks** described in section 1 and section 3.

## 5a. How to test after a design change

Run these from the project folder, in this order. None of them touch `data/food_bank.db`.

| Command | What it checks | Time |
| --- | --- | --- |
| `run_tests_windows.bat` | All automated tests (should report `OK` and 0 skipped). `tests/test_searchable_inventory.py` and `tests/test_inventory_view_and_seed.py` render `inventory.html` and `other_banks.html` directly; `tests/test_multipage_ui.py` loads every page for each role. | about 2 min |
| `.venv\Scripts\python.exe verify_ui.py` | Starts the API and three nodes on spare ports, then uses every page like a browser (67 steps): navigation and active links, search, filters and sorting, other banks, request → approve → ship → receive, quantities at both banks, donations, batches and recalls, transportation, more tools, role visibility and CSRF, admin page, outage and sync, old links, logout. Every step prints PASS or FAIL. | about 1 min |
| `.venv\Scripts\python.exe verify_distributed.py` | The full live multi-node check (18 scenarios) | about 1.5 min |
| By hand | Open each page as `staff`, `admin` and a `viewer` (create one on the admin page) at 375 px and 1280 px wide. Click every sidebar link and open the Menu button at phone width. | 10 min |

If `verify_ui.py` reports a FAIL after a redesign, check the step name first. Usually a section id, button label or form field was changed. Either restore it, or (if the change is intended) update the check in `verify_ui.py` and this document together.

If you need data the API does not provide yet, ask the backend team to add a field. Do not compute it in the template.

## 6. Backend behavior that must not change

- **Nodes only talk to the central API.** Never read or write the SQLite database from a template, from JavaScript or from `node.py`.
- **CSRF checks, the per-node session cookie name (`foodbank_node_<id>`) and the "session belongs to this bank" check** in `node.py` must stay.
- **Never put the API token in the page, in JavaScript or in browser storage.** It stays in the node's signed, HttpOnly session cookie, which page scripts cannot read.
- **Redirect targets come from the fixed tables in `node.py`** (`RETURN_PAGES`, `ADVANCED_ACTION_PAGES`) and the old-link maps in `dashboard.html` and `moved.html`. Never build a redirect target from a form field or query parameter.
- **Other banks' private data** (physical quantity, target, reservations, lot details) is removed by the API. Do not try to work around this, and do not fetch it to hide it with CSS.
- **Transfer states and stock movements** (reserve on approve, deduct on ship, credit on receive) are enforced by the API. The UI should only offer the actions listed in section 4.
- **Idempotency keys** (`request_key`, offline `op_key`) must stay unique per operation.
- **Offline conflicts must be shown to a person**, never resolved automatically.
- **No reset or delete-database buttons** on any page. Live restore is a manual procedure (`DATABASE_MAINTENANCE.md`).
- **Numbers on the admin page must come from the API.** Do not add placeholder figures.
