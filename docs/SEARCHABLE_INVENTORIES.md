# Searchable Inventories: Search, Filter and Sort Parameters

The node website has **two independent sets of search controls**, each on its own page:

1. **My inventory** (`/my-inventory`, section `#inventory`) defaults to read-only. View and Edit tabs stay above the table. Both modes offer a case-insensitive item/category/unit search, a category selector, and stock-status filters. Edit mode preserves the active filters after saving, deleting, or adding an item. Staff/managers/admins can edit only their own bank; viewers cannot edit.
2. **Other food banks** (`/other-banks`, section `#other-inventories`) has a separate tab for every *other* registered bank. Each tab loads only that bank's catalogued inventory from the central API. Its own search/category/availability controls are independent from the local-bank search. Switching to another bank clears the remote search so results never appear to belong to the wrong bank. No other-bank edit controls are provided.

## My inventory filters

- `q`: partial item name, category, or unit, case-insensitive
- `category`: exact matching category, case-insensitive (`All categories` disables filter)
- `status`: `all`, `in_stock`, `out_of_stock`, `shortage`, `surplus`, `expired`, `at_target`
- `inventory=edit`: edit mode if the signed-in role has write permission
- `sort`: `name` (default), `category`, `quantity`, `shortage`, `surplus`, `expiration`. Unknown values fall back to `name`.

Example: `http://127.0.0.1:5001/my-inventory?q=rice&status=shortage`

Old bookmarks such as `http://127.0.0.1:5001/?q=rice&status=shortage` are redirected to this page with the same (validated) filters.

## Other-bank filters

- `other_bank`: selected **other** bank ID, constrained to `GET /api/banks` results
- `other_q`: partial item name, category, or unit
- `other_category`: category for the selected bank
- `other_status`: `all`, `available`, `unavailable`
- `other_sort`: `name` (default), `category`, `available`

Example: `http://127.0.0.1:5001/other-banks?other_bank=2&other_q=beans&other_status=available`

The table has item name, category, unit, **available-to-share surplus**, and an availability label. **It does not show another bank's physical quantity, target, reserved stock, lot information, or shortage counts**. Items not currently available for exchange still appear (with 0 available), so people can browse each separate catalog. Transfers should be initiated through the existing matching/request workflow.

The bank selector is generated from `GET /api/banks`, so additional banks created by the administrator appear automatically. No JavaScript framework is required.

## Central API search (authenticated)

`GET /api/banks/{bank_id}/inventory` now also supports optional `q`, `category`, and `status` query parameters. For *other* banks, the only statuses are `all`, `available`, and `unavailable`. For the logged-in bank, statuses are the full local list above. The response contains the selected `bank`, `inventory`, `total`, `filtered_total`, `categories`, `filters`, and `shared` boolean. Existing consumers can still read `bank` and `inventory` unchanged.

Example calls:

```text
GET /api/banks/2/inventory?q=rice&category=Dry%20Goods&status=available
GET /api/banks/1/inventory?status=shortage
```

## Files for the frontend teammate

- `templates/dashboard.html`: both forms, bank tabs, tables and accessibility labels
- `node.py`: selected-bank resolution and UI data binding, no direct SQLite access
- `inventory_filters.py`: shared query normalization and filtering rules
- `routes/inventory.py`: authenticated, bank-scoped API filtering and redacted remote data
- `static/style.css`: minimal plain-form spacing
- `tests/test_searchable_inventory.py`: filtering, access scope, rendered HTML, optional Flask API test

Do not remove CSRF hidden fields from POST forms. Never send another bank's private inventory totals to a page just to hide them with CSS.

## Testing

Run `python -m unittest tests.test_searchable_inventory tests.test_inventory_view_and_seed -v` for these features. Run the full suite as well once Flask is installed. The live Flask HTTP integration test is skipped when Flask is not installed, and a run with skipped tests is not a full pass.
