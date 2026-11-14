# Test Plan

This document explains how to test the Food Bank Database, both automatically and by hand. Every automated check uses temporary databases. None of them change `data/food_bank.db`. Our latest results are in section 10 of `FINAL_TECHNICAL_REPORT.md`.

## 1. Automated checks

| Command | What it does | About how long |
| --- | --- | --- |
| `run_tests_windows.bat` or `python -m unittest discover -s tests -v` | All unit and integration tests | 1-2 min |
| `run_release_check_windows.bat` or `python release_check.py` | Tests, plus the 8 test-lab scenarios, a scripted transfer story, a 5/10/20-bank SQLite benchmark and a Docker Compose syntax check. Writes `reports/release_verification.md` and `.json`. | 2-3 min |
| `python verify_distributed.py` | Starts the real API and 3 real nodes on spare ports (default from 25000) with a temporary database, and runs 18 scenarios over HTTP | about 1.5 min |
| `python verify_ui.py` | Starts the API and 3 nodes on spare ports (default from 29000) with a fresh fictional database and walks through every page like a browser (67 steps: navigation and active links, search, filters and sorting, other banks, a full transfer, donations, batches and recalls, transportation, more tools, role visibility and CSRF, admin page, outage and sync, old links, logout) | under 1 min |
| `python run_lab.py --scenario all` (or `run_lab_windows.bat`) | Just the 8 test-lab scenarios | under 1 min |
| `python presentation_story.py` | The scripted donation-to-receipt story with an outage | seconds |
| `verify_docker_windows.bat` | Builds and starts the Docker Compose setup and checks it. Needs Docker Desktop running and ports 5000-5003 free. | several minutes |

Run the tests with the project's virtual environment (`.venv\Scripts\python.exe`), so that Flask is available. If Flask is missing, the Flask-based tests are skipped. A run with skipped tests does not count as a full pass.

## 2. Test files

| File | Covers |
| --- | --- |
| `test_business_logic.py` | Schema setup, validation, reserve/ship/receive, concurrent approvals, retries, expiration, history |
| `test_expanded_features.py` | Donations, waste, lots, CSV import/export, barcodes, scheduling, offline adjustments |
| `test_final_features.py` | Recalls, storage, physical counts, transport, partial delivery, offline receipts |
| `test_release_features.py` | Isolated presentation sessions, the readiness snapshot, the 5-bank benchmark and the scripted story |
| `test_inventory_view_and_seed.py` | Sample data and the inventory view/edit tabs |
| `test_searchable_inventory.py` | Search and filters, and hiding other banks' private fields |
| `test_api.py`, `test_extended_api.py`, `test_final_api.py`, `test_release_api.py` | Flask API and node tests: login, roles, bank scoping, CSRF, tracing, live HTTP between a node and the API |
| `test_reliability.py` | Database connections are always closed, and no unneeded upgrade backups |
| `test_database_admin.py` | Database administration API and page, backups, restore tests, health checks |
| `test_node_sessions_and_sync.py` | Separate node session cookies, and offline sync continuing past a rejected change |
| `test_release_fixes.py` | Shortage ignores expired and recalled stock; lot-expiry form works without JavaScript; storage form only accepts known types; all roles offered in the dashboard "Add user" form |
| `test_security.py` | Outage simulation (admin only, demo mode only, local only, logged, state unchanged on refusal); demo accounts only in demo mode; password and token hashing; `manage_accounts.py`; direct API attempts at cross-bank access and privilege escalation; JSON 413/500 errors without internal details |

## 3. The 18 live distributed scenarios (`verify_distributed.py`)

1. Start the central API and three separate bank nodes
2. Each node logs in and reads its own inventory
3. A change at one bank is visible, correctly, from another
4. A transfer from request through receipt across nodes
5. One node disconnects while the others keep working
6. Supported offline actions are queued safely
7. Reconnect and synchronize
8. Stale updates and sync conflicts
9. Repeated additive operations are applied once
10. Simultaneous requests for the same supplies
11. Retried API calls do not duplicate transfers
12. The central API is stopped and restarted
13. Failed operations give clear errors and the system recovers
14. Controlled load with 5, 10 and 20 banks
15. Backup and isolated restore checks
16. Node health, trace IDs, status endpoints and event logs
17. Only administrators can simulate an outage, and recovery works
18. Each bank searches its inventory independently, with no cross-bank edits

The script runs the processes in classroom demo mode on an isolated temporary database. It writes a JSON report (`--out` sets the path) and exits with code 0 only if every scenario passed.

## 4. Manual test checklist

Start `run_presentation_windows.bat`. Open Central (5001) and Northside (5002) in two browser windows and log in as `staff` / `demo123`.

| # | Steps | Expected result |
| --- | --- | --- |
| 1 | Log in on Central, then on Northside, in the same browser | Both stay logged in |
| 2 | On Central, search "rice" and filter by Shortage | Rice shows a shortage of 15 |
| 3 | Open the Northside tab under other food banks' inventories | Only name, category, unit and available amount are shown, with no physical quantity or target |
| 4 | In Available supply matches, request 10 bags of Rice from Northside | Request appears under Central's "Requests we made" as pending |
| 5 | On Northside, approve | Status approved. Northside's reserved amount goes up by 10 and its quantity stays the same |
| 6 | On Northside, ship | Status in_transit. Northside's quantity drops by 10 |
| 7 | On Central, receive | Status received. Central's Rice quantity goes up by 10 |
| 8 | Click receive again (or resend the form) | Error message, no change to stock |
| 9 | Log in on Central as `admin` / `admin123`. On the **Offline sync** page, click **Simulate connection outage**, queue +3 on an item, **Restore simulated connection**, **Attempt sync** | "Synchronized 1 changes; 0 conflicts" and the quantity goes up by 3 once |
| 9a | As Central `staff`, open the **Offline sync** page | No outage button, only the connection status. A direct POST to `/offline/simulate` returns 403. |
| 10 | Simulate the outage again and queue +1 on an item. Then, in another window, change that item's quantity. Restore and sync. | The change is marked as a conflict, and the central quantity is not changed by it |
| 11 | Start the four processes in separate terminals (see README section 5), then press Ctrl+C in the `server.py` terminal and reload a node. (The launchers stop every process when one stops.) | Offline page with the last snapshot, and the node keeps running |
| 12 | Log in as `admin` on Central and open Database administration. Create a verified backup, then click "Test restore (isolated)". | Backup shows as verified, and the restore test passes |
| 13 | Log in as a `viewer` account (create one on the admin page) | No edit tab, and no buttons that change data |
| 14 | Visit http://127.0.0.1:5000/docs and try `/api/health` | `{"status": "ok", ...}` |

## 5. What is not covered

- Running the nodes and the API on separate computers
- Docker runtime (only run if `verify_docker_windows.bat` is used on a machine with Docker Desktop)
- Browser compatibility and accessibility testing beyond manual checks
- Security testing beyond the role, scope and CSRF tests
