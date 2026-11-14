# Final Test Results

All results below come from one Windows 11 computer with Python 3.13.3, Flask 3.1.3 and Docker Desktop (Docker 29.4.1). Every run used temporary databases. No existing project database was used, no results are estimated, and skipped tests are never counted as passed. No test was skipped in any run below.

## 1. Summary (2026-11-11)

| Check | Passed | Failed | Skipped | Notes |
| --- | ---: | ---: | ---: | --- |
| `python -m unittest discover -s tests` (unit and integration tests) | **156** | **0** | **0** | Run with `FOOD_BANK_DEMO_MODE` unset in the shell, as a student would type it |
| `tests/test_release_fixes.py` (regression tests) | 8 | 0 | 0 | See section 2 |
| `verify_ui.py` (scripted interface walkthrough) | **67 steps** | 0 | — | 0 tracebacks, 0 HTTP 500 responses, no ports left open |
| `verify_distributed.py` (live API + three nodes) | **18 scenarios / 148 checks** | 0 | — | No timeouts, no ports left open |
| `release_check.py` | pass | — | — | 156 tests, fault lab 8/8, scripted presentation 7/7, 3 SQLite benchmark cases, Compose syntax valid |
| `verify_docker.py` (Docker Compose runtime) | pass | 0 | — | API health, health of all three nodes, and an authenticated API call |

**Docker fix.** The first Docker run failed: the API container exited because the non-root `foodbank` user could not create the database. The `Dockerfile` changed the owner of `/app/data` before `COPY . .`, and the copy recreated the folder owned by root. The ownership change now runs after the copy, and the Docker check passes.

## 2. Regression tests for late bug fixes

`tests/test_release_fixes.py` covers four bugs that were fixed late in development:

| Bug fixed | Tests |
| --- | --- |
| Expired and recalled lots were counted as usable stock, which could hide a shortage | `test_expired_stock_does_not_hide_a_shortage`, `test_recalled_stock_does_not_hide_a_shortage`, `test_expired_shortage_now_produces_matches` |
| The batch-expiration form needed JavaScript | `test_lot_expiry_without_javascript_uses_entered_batch`, `test_lot_expiry_rejects_missing_batch`, `test_lot_expiry_with_rewritten_url_still_works` |
| The storage form accepted any storage type | `test_storage_form_rejects_unknown_storage_type` (only `ambient`, `refrigerated` and `frozen` are accepted) |
| The "Add user" form did not offer every role | `test_dashboard_add_user_form_offers_every_role` (`staff`, `viewer`, `driver`, `dispatcher`, `manager`, `admin`) |

All 8 tests pass.

## 3. What the automated tests cover

The 156 tests in `tests/` cover:
- the API: authentication, inventory, lots, matching, transfers, donations, recalls, scheduling and administration;
- business rules: shortage and surplus calculations, transfer states and stock reservation;
- the database administration page: health checks, backups and isolated restore tests;
- the offline queue and sync, retries and repeated requests;
- security (`test_security.py`): the admin-only outage simulation, demo accounts only in demo mode, salted password hashes, hashed session tokens, cross-bank access, role checks on admin endpoints, and JSON error responses that hide internal details;
- the multi-page interface (`test_multipage_ui.py`): every page loads for each role, read-only roles see no change controls and the server still refuses those actions, CSRF protection, and safe redirects;
- searching, filtering and sorting inventories.

## 4. Interface walkthrough (`verify_ui.py`, 67 steps)

The walkthrough starts the real API and nodes and goes through: sidebar navigation and the active link on every page; search, filters and sort; View and Edit modes and adding an item; read-only views of other banks; supply matching; a full Central ← Northside Rice transfer (request, approve with reservation, ship −10, receive +10); donations and waste; batches and recalls; transportation; viewer and driver visibility, including a viewer's direct form post being refused and CSRF rejection; the Administration page, health check and audit records; an admin-only simulated outage, with queued changes synced exactly once; the System status page; old links redirecting safely; and logout.

## 5. Live multi-node verification (`verify_distributed.py`)

The script starts the real central API and three node processes on an isolated temporary database. Result: 18/18 scenarios and 148/148 checks. Every check is listed in `DISTRIBUTED_VERIFICATION.md`.

| Check | Scenario |
| --- | --- |
| Start the API and three separate nodes | 1 |
| Authentication | 2 |
| Independent search and filtering per bank | 18 |
| Transfer between banks | 4 |
| Cross-bank permissions | 2, 13, 18 |
| Administrator-authorized outage | 5, 8, 17 |
| Ordinary users cannot start or end an outage | 17 |
| Reconnect and recovery | 7, 12, 17 |
| Clean shutdown | after the run |

## 6. Earlier security and robustness testing

These checks were run earlier in development. The later changes were mainly to the page layout, templates and redirects. The automated tests above still cover the same rules, but these larger probes were not repeated on the final version.

- **Cross-bank probe:** 55 direct API attempts to read or change another bank's data. Every attempt that should be refused was refused (403, 404 or 409). The attempts that succeeded were correctly limited to the user's own bank.
- **API fuzzing:** 3,366 malformed requests (wrong types, missing fields, huge or negative numbers, invalid dates, 100 KB strings, SQL and path-traversal text) caused 0 server errors and revealed no internal details.
- **Node form fuzzing:** 4,644 requests with garbage form and query values found no crash caused by form input.
- **Outside demo mode:** a new database got no accounts, and on a copy of a demo database the demo passwords were refused (403, recorded in the audit log).
- **Database quality:** 22 checks passed, including integrity and foreign keys, inventory totals matching lot totals, reservations, exclusion of expired and recalled stock, backup and restore, and persistence after restart.
- **Layout:** every page was loaded in headless Chrome at 375, 768 and 1280 px wide with no horizontal page scrolling.

## 7. Not verified

- **Separate computers or a real network.** All processes ran on one machine.
- **Docker on Linux or macOS.** Docker was tested on Windows only.
- **Screen readers and real mobile devices.** Layout was checked automatically only.
- **Independent security review or penetration test.** None was done. The checks above are our own tests.
