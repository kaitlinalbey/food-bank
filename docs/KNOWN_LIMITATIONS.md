# Known Limitations

This is a classroom prototype. The limitations below are known and documented. They are not hidden defects. Each one was checked against the current code.

## Resolved in the security release

| Former limitation | What changed |
| --- | --- |
| Any logged-in user could start or stop the simulated connection outage for a whole node | Now limited to administrators, only in classroom demo mode, and only from the same computer. Every attempt is logged, and refused attempts change nothing (`node.py`). |
| During a real outage, the offline page's "Retry" button posted to the simulation toggle and could switch a simulated outage on | "Retry" is now a plain reload link (`templates/offline.html`). |
| `admin`/`admin123` and `staff`/`demo123` were created automatically in every new database, and `admin` was added to any database without one | Demo accounts are created only in explicit demo mode. Outside demo mode, demo passwords are refused at login, and `manage_accounts.py` sets up real accounts (`database.py`, `routes/auth.py`). |
| Oversized requests and unexpected server errors returned HTML | Both now return JSON. A 500 response contains only a generic message and the request ID (`app.py`). |

## Architecture

| Limitation | Effect | Where |
| --- | --- | --- |
| One central API and one SQLite database | If either is down, no transfers or other cross-bank actions can happen. There is no replication, failover or leader election. | `server.py`, `database.py` |
| All processes run on one computer in the demo | Real network delays and machine failures were not tested. Docker Compose also runs on one host. | `start_demo.py`, `compose.yaml` |
| Every API request writes a trace row | Writes are serialized even for read requests. In our load tests, throughput stayed at about 34-44 requests per second regardless of the number of clients. | `app.py` (`capture_event`) |
| Nodes learn about changes only when a page is loaded | There is no push update between nodes. | `node.py` |

## Offline mode

- Only stock adjustments and new donation receipts can be queued offline. Requests, approvals, shipping and receiving need the API.
- A queued stock adjustment is rejected if the item changed while the node was offline. Staff must review it and enter it again.
- Only one unsynced adjustment per item is allowed.
- When an administrator starts the simulated outage, it affects every user of that node until it is switched back. That is intended for the demo.
- In Docker Compose, browser requests reach the nodes through Docker's network rather than from the same computer, so the outage simulation is refused there.

## Security (classroom level only)

- The demo passwords are public. They work whenever demo mode is on, and the Windows launchers and Docker Compose always turn it on. Anyone who can start the servers can enable demo mode.
- There is no HTTPS in the demo. The node keeps the API token in the browser's session cookie, which is signed and HttpOnly but not encrypted. The token is never placed in a page.
- Login throttling is per account, not per IP address, so anyone can lock out a known username for 15 minutes.
- There is no password reset, multi-factor login, or password change page in the web interface. Passwords can be changed with `manage_accounts.py`.
- There has been no independent security review or penetration test. Our checks were automated: about 3,400 malformed API requests, about 4,600 malformed node form requests, and direct cross-bank attempts through the API. See `FINAL_TEST_RESULTS.md`.

## Features

- Matching uses straight-line distance between sample coordinates. There is no routing or delivery cost.
- Storage and vehicle capacity use generic "units", not weight or volume.
- Recalls work only on exact lot codes entered by an admin. There is no connection to real recall notices.
- The driver role is read-only and has no task screen.
- A supplier cannot cancel a transfer it has already approved. Only the requesting bank can cancel.
- Bank names for banks 1-3 are built into `node.py`. A node for a bank added later shows "Food Bank #N" in its header.
- CSV export adds a `'` in front of cells that start with formula characters. This protects spreadsheets, but the `'` stays if the file is imported again.
- `templates/error.html` is not used by any route.
- Inventory tables are sorted by item name and filtered by search, but they have no pagination. With the sample data (48 items per bank) the tables stay usable. A bank with several hundred items would need pagination or column sorting.
- The interface is split into separate pages with a sidebar. Inventory and transfer tables have no pagination, which is fine for 48 items but would need it for several hundred. Old links to anchors on the former single page (for example `/#incoming`) are forwarded by a short script, so without JavaScript they stop at the Dashboard.

## Portability: Windows batch-file line endings

In the team's working copy, the `.bat` launchers are saved with Unix (LF) line endings. Windows `cmd.exe` normally expects Windows (CRLF) line endings, and LF-only batch files can misread `goto` labels or multi-line blocks on some systems. Both `run_presentation_windows.bat` and `run_windows.bat` worked correctly with LF endings on our test machine, so this is a portability consideration, not a confirmed defect. The repository's `.gitattributes` file contains `*.bat text eol=crlf`, so Git checks the launchers out with CRLF endings on Windows.

## Verification gaps

- The Docker Compose runtime was tested on one Windows computer with Docker Desktop (`verify_docker.py`: API health, all three node health checks and an authenticated API call). It was not tested on Linux or macOS hosts.
- Browser layout and accessibility were checked automatically: every page loaded and measured for horizontal overflow at 375, 768 and 1280 px in headless Chrome, plus link and form checks. They were not checked with a screen reader or on real mobile devices.
- We have no measurements from separate computers or a real network.
